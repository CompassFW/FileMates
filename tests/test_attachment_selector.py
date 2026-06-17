"""--attachment <substr>: file only the attachments whose original filename matches.

Real production failure (2026-06-16): a tax-advisor email carries SEVERAL distinct
documents (an invoice, a UStVA protocol, a missing-receipts list). The downloader files
ALL attachments of a mail into ONE folder under ONE naming scheme, so the three documents
collapsed onto the same name (→ _v1/_v2 collisions) in the wrong folder. The selector lets
the orchestrating agent file each document separately — one call per document, each with
its own --type (sub-folder) and --name-sender/--name-type — by matching the attachment's
original filename.
"""
from email.message import EmailMessage

import fetch_attachments as fa

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<<>>\n%%EOF\n"


def _make_raw():
    m = EmailMessage()
    m["From"] = "Steuerkanzlei Muster <advisor@example.com>"
    m["To"] = "me@example.com"
    m["Subject"] = "Unterlagen Mai 2026"
    m["Date"] = "Sun, 07 Jun 2026 10:00:00 +0000"
    m.set_content("siehe Anhang")
    # three DISTINCT documents in one mail (each byte-distinct so a no-filter run collides on name)
    m.add_attachment(MINIMAL_PDF + b"A", maintype="application", subtype="pdf",
                     filename="Rechnung 2026-37.pdf")
    m.add_attachment(MINIMAL_PDF + b"BB", maintype="application", subtype="pdf",
                     filename="UStVA Mai.pdf")
    m.add_attachment(MINIMAL_PDF + b"CCC", maintype="application", subtype="pdf",
                     filename="Fehlende Belege.pdf")
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
        "- `naming_scheme:` <sender>_<type>_<TT-MM-JJJJ>\n",
        encoding="utf-8",
    )
    target = tmp_path / "R"
    target.mkdir(exist_ok=True)
    argv = ["x", "--config", str(cfg), "--folder", str(target), "--no-trash"] + (extra or [])
    monkeypatch.setattr(fa.sys, "argv", argv)
    fa.main()
    return target


def test_attachment_files_only_the_matching_document(monkeypatch, tmp_path):
    target = _run(monkeypatch, tmp_path,
                  ["--type", "receipts", "--attachment", "UStVA",
                   "--name-sender", "Steuerkanzlei", "--name-type", "USt-VA"])
    assert [p.name for p in target.glob("*.pdf")] == ["Steuerkanzlei_USt-VA_07-06-2026.pdf"]


def test_attachment_match_is_case_insensitive(monkeypatch, tmp_path):
    target = _run(monkeypatch, tmp_path,
                  ["--type", "receipts", "--attachment", "rechnung",
                   "--name-sender", "Steuerkanzlei", "--name-type", "Rechnung"])
    assert [p.name for p in target.glob("*.pdf")] == ["Steuerkanzlei_Rechnung_07-06-2026.pdf"]


def test_without_attachment_all_three_are_filed(monkeypatch, tmp_path):
    # regression: the legacy behaviour (all attachments) is unchanged when the flag is absent.
    target = _run(monkeypatch, tmp_path, ["--type", "receipts"])
    assert len(list(target.glob("*.pdf"))) == 3


def test_attachment_no_match_files_nothing(monkeypatch, tmp_path):
    target = _run(monkeypatch, tmp_path, ["--type", "receipts", "--attachment", "kontoauszug"])
    assert list(target.glob("*.pdf")) == []
