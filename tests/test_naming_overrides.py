"""Naming overrides --name-sender / --name-type (G-01, 2026-06-09).

The owner's binding convention is `Anbieter_Typ_TT-MM-JJJJ` — but the FROM domain is not
always the vendor (an ElevenLabs invoice arrives via stripe.com), and the CLI --type is an
English folder key (receipts), not the German document type (Beleg/Rechnung). These two
optional flags let the orchestrating agent supply the REAL vendor and the German type for
NAMING ONLY; folder choice stays with --type, and render_name's sanitisation still applies
(an override can never escape the target folder).
"""
from email.message import EmailMessage

import fetch_attachments as fa

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<<>>\n%%EOF\n"


def _make_raw():
    m = EmailMessage()
    m["From"] = "Stripe <invoices@stripe.com>"          # payment processor, NOT the vendor
    m["To"] = "me@example.com"
    m["Subject"] = "Your invoice"
    m["Date"] = "Mon, 01 Jun 2026 10:00:00 +0000"
    m.set_content("see attachment")
    m.add_attachment(MINIMAL_PDF, maintype="application", subtype="pdf",
                     filename="invoice.pdf")
    return m.as_bytes()


class FakeServer:
    def __init__(self, raw):
        self._raw = raw
        self.capabilities = ()

    def select(self, mailbox, readonly=False):
        return "OK", [b"1"]

    def list(self):
        return "OK", [b'(\\HasNoChildren \\Trash) "/" "Trash"']

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b"1"]
        if command == "FETCH":
            return "OK", [(b"1 (RFC822)", self._raw)]
        return "OK", [b""]

    def logout(self):
        return "OK", [b""]


def _run(monkeypatch, tmp_path, extra=None):
    monkeypatch.setattr(fa.imaplib, "IMAP4_SSL", lambda *a, **k: FakeServer(_make_raw()))
    monkeypatch.setattr(fa, "imap_login", lambda *a, **k: "FAKE")
    monkeypatch.setattr(fa, "load_delete_after_senders", lambda: set())
    monkeypatch.setattr(fa, "load_protected_senders", lambda: set())
    cfg = tmp_path / "config.local.md"
    cfg.write_text(
        "- `imap_host:` imap.example.com\n"
        "- `imap_user:` me@example.com\n"
        "- `naming_scheme:` <sender>_<type>_<TT-MM-JJJJ>\n",   # the owner's convention
        encoding="utf-8",
    )
    target = tmp_path / "R"
    target.mkdir(exist_ok=True)
    argv = ["x", "--config", str(cfg), "--folder", str(target), "--no-trash"] + (extra or [])
    monkeypatch.setattr(fa.sys, "argv", argv)
    fa.main()
    return target


def test_overrides_produce_convention_name(monkeypatch, tmp_path):
    # the agent knows the real vendor + German type -> file follows the convention exactly.
    target = _run(monkeypatch, tmp_path,
                  ["--type", "receipts", "--name-sender", "ElevenLabs",
                   "--name-type", "Rechnung"])
    assert [p.name for p in target.glob("*.pdf")] == ["ElevenLabs_Rechnung_01-06-2026.pdf"]


def test_without_overrides_behaviour_unchanged(monkeypatch, tmp_path):
    # regression: no flags -> exactly the old name (From-domain + CLI type).
    target = _run(monkeypatch, tmp_path, ["--type", "receipts"])
    assert [p.name for p in target.glob("*.pdf")] == ["stripe_receipts_01-06-2026.pdf"]


def test_override_only_sender(monkeypatch, tmp_path):
    target = _run(monkeypatch, tmp_path, ["--type", "receipts", "--name-sender", "ElevenLabs"])
    assert [p.name for p in target.glob("*.pdf")] == ["ElevenLabs_receipts_01-06-2026.pdf"]


def test_name_type_never_leaks_into_folder_choice(monkeypatch, tmp_path):
    # the docstring promise "folder choice stays with --type": resolve the folder via
    # the CONFIG MAP (no --folder shortcut), with a --name-type that has NO map entry.
    # If the override leaked into resolve_folder, the file would miss the mapped dir.
    monkeypatch.setattr(fa.imaplib, "IMAP4_SSL", lambda *a, **k: FakeServer(_make_raw()))
    monkeypatch.setattr(fa, "imap_login", lambda *a, **k: "FAKE")
    monkeypatch.setattr(fa, "load_delete_after_senders", lambda: set())
    monkeypatch.setattr(fa, "load_protected_senders", lambda: set())
    mapped = tmp_path / "BelegeOrdner"
    mapped.mkdir()
    cfg = tmp_path / "config.local.md"
    cfg.write_text(
        "- `imap_host:` imap.example.com\n"
        "- `imap_user:` me@example.com\n"
        "- `naming_scheme:` <sender>_<type>_<TT-MM-JJJJ>\n"
        f"| receipts | {mapped} |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(fa.sys, "argv",
                        ["x", "--config", str(cfg), "--type", "receipts", "--no-trash",
                         "--name-sender", "ElevenLabs", "--name-type", "Rechnung"])
    fa.main()
    assert [p.name for p in mapped.glob("*.pdf")] == ["ElevenLabs_Rechnung_01-06-2026.pdf"]


def test_override_is_sanitised_no_path_escape(monkeypatch, tmp_path):
    # a hostile/buggy override value cannot escape the target folder or smuggle separators.
    target = _run(monkeypatch, tmp_path,
                  ["--type", "receipts", "--name-sender", "../../etc",
                   "--name-type", "x/../y"])
    files = list(target.glob("*.pdf"))
    assert len(files) == 1                                # filed INSIDE the target
    # what matters for safety: no path separator survives and the name can't be a
    # dotfile/relative segment (a literal ".." INSIDE a filename is harmless without
    # separators — render_name keeps dots but strips them at the ends).
    assert "/" not in files[0].name and "\\" not in files[0].name
    assert not files[0].name.startswith(".")
    assert files[0].resolve().parent == target.resolve()  # second escape check
    assert list(tmp_path.glob("*.pdf")) == []             # nothing escaped upwards
