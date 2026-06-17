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


# --------------------------------------------------------------------------- #
# Trash-enabled selective fetch: a selective --attachment run is partial BY
# DESIGN, so the mail MUST be kept even when the selected document is the only
# recognised attachment AND the sender is on the delete-after-filing list.
# (Without --no-trash this is the path the earlier selector tests never reached,
# so the never-trash guarantee was untested — a surviving mutant.)
# --------------------------------------------------------------------------- #
ENCRYPTED_PDF = b"%PDF-1.4\n1 0 obj<< /Type /Catalog >>endobj\ntrailer<< /Encrypt 9 0 R /Root 1 0 R >>\n%%EOF"


def _make_single(filename, payload=MINIMAL_PDF, from_addr="Shop <news@shop.example>"):
    m = EmailMessage()
    m["From"] = from_addr
    m["To"] = "me@example.com"
    m["Subject"] = "Beleg"
    m["Date"] = "Sun, 07 Jun 2026 10:00:00 +0000"
    m.set_content("siehe Anhang")
    m.add_attachment(payload, maintype="application", subtype="pdf", filename=filename)
    return m.as_bytes()


class TrashRecordingServer(FakeServer):
    """FakeServer that records every uid() command so a test can assert whether a
    trash action (STORE +X-GM-LABELS \\Trash) was issued."""

    def __init__(self, raw):
        super().__init__(raw)
        self.uid_calls = []

    def uid(self, command, *args):
        self.uid_calls.append((command, args))
        return super().uid(command, *args)

    def trashed(self):
        return any(cmd == "STORE" and any("\\Trash" in str(a) for a in args)
                   for cmd, args in self.uid_calls)


def _run_trash(monkeypatch, tmp_path, raw, delete_after, extra):
    server = TrashRecordingServer(raw)
    monkeypatch.setattr(fa.imaplib, "IMAP4_SSL", lambda *a, **k: server)
    monkeypatch.setattr(fa, "imap_login", lambda *a, **k: "FAKE")
    monkeypatch.setattr(fa, "load_delete_after_senders", lambda: set(delete_after))
    monkeypatch.setattr(fa, "load_protected_senders", lambda: set())
    cfg = tmp_path / "config.local.md"
    cfg.write_text(
        "- `imap_host:` imap.example.com\n"
        "- `imap_user:` me@example.com\n"
        "- `naming_scheme:` <sender>_<type>_<TT-MM-JJJJ>\n"
        "- `delete_mode:` gmail-trash\n",   # cached reversible mode -> can_delete True (no probe)
        encoding="utf-8",
    )
    target = tmp_path / "R"
    target.mkdir(exist_ok=True)
    # NOTE: deliberately NO --no-trash, so the trash path is genuinely reachable.
    argv = ["x", "--config", str(cfg), "--folder", str(target), "--type", "receipts"] + extra
    monkeypatch.setattr(fa.sys, "argv", argv)
    fa.main()
    return target, server


def test_selective_fetch_keeps_delete_after_mail_even_single_doc(monkeypatch, tmp_path):
    # Sender IS on the delete-after list and trashing is ENABLED, but the run is a
    # selective --attachment fetch of the mail's only document -> the mail must be KEPT
    # (it is partial by design). Mutating the seed `msg_has_unfiled = bool(args.attachment)`
    # to False makes this mail get trashed -> this test goes red.
    raw = _make_single("UStVA Mai.pdf", from_addr="Shop <news@shop.example>")
    target, server = _run_trash(monkeypatch, tmp_path, raw, {"news@shop.example"},
                                ["--attachment", "UStVA"])
    assert [p.name for p in target.glob("*.pdf")] == ["shop_receipts_07-06-2026.pdf"]  # filed
    assert server.trashed() is False                                                  # but NOT trashed


def test_selective_fetch_of_a_filed_mail_without_attachment_DOES_trash(monkeypatch, tmp_path):
    # Control: the SAME mail/sender WITHOUT --attachment is trashed after verified filing
    # (proves the keep above is caused by the selective fetch, not by a broken trash path).
    raw = _make_single("UStVA Mai.pdf", from_addr="Shop <news@shop.example>")
    target, server = _run_trash(monkeypatch, tmp_path, raw, {"news@shop.example"}, [])
    assert len(list(target.glob("*.pdf"))) == 1
    assert server.trashed() is True


def test_selective_fetch_matching_encrypted_pdf_quarantines_and_keeps(monkeypatch, tmp_path):
    # --attachment composes with the encrypted-PDF branch (which sits AFTER the filter):
    # the matching encrypted doc is quarantined, never filed flat, and the mail is kept.
    raw = _make_single("UStVA verschluesselt.pdf", payload=ENCRYPTED_PDF,
                       from_addr="Shop <news@shop.example>")
    target, server = _run_trash(monkeypatch, tmp_path, raw, {"news@shop.example"},
                                ["--attachment", "UStVA"])
    assert list(target.glob("*.pdf")) == []                       # not filed flat
    assert list(target.rglob("_Passwortgeschuetzt/*.pdf"))        # quarantined in the sub-folder
    assert server.trashed() is False                              # encrypted -> mail kept


def test_selective_fetch_matching_empty_payload_files_nothing_and_keeps(monkeypatch, tmp_path):
    raw = _make_single("UStVA leer.pdf", payload=b"", from_addr="Shop <news@shop.example>")
    target, server = _run_trash(monkeypatch, tmp_path, raw, {"news@shop.example"},
                                ["--attachment", "UStVA"])
    assert list(target.glob("*.pdf")) == []
    assert server.trashed() is False
