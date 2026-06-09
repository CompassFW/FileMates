"""Integration tests that drive the real main() through a fake IMAP server.

The unit tests prove the deletion DECISIONS; these prove the PRODUCTION WIRING in main()
actually uses them. They would catch an un-wiring that the helper-level tests miss:
  - filing failure must leave the mail untouched (no trash command issued),
  - a verified-filed delete-after sender IS trashed via the chosen reversible mechanism,
  - re-running does not pile up duplicate files (dedup), and
  - copy-trash without UIDPLUS never triggers an expunge.
The fake models the IMAP command/response contract and records every command issued.
"""
from email.message import EmailMessage

import fetch_attachments as fa

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<<>>\n%%EOF\n"


def _make_raw(from_h, filename, payload, maintype, subtype, subject="Rechnung Mai"):
    m = EmailMessage()
    m["From"] = from_h
    m["To"] = "me@example.com"
    m["Subject"] = subject
    m["Date"] = "Mon, 01 Jun 2026 10:00:00 +0000"
    m.set_content("see attachment")
    m.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)
    return m.as_bytes()


def _make_raw_mixed_inline(from_h, subject="Rechnung Mai"):
    """A mail with a normal (filed) PDF attachment AND a second PDF carried as
    Content-Disposition: inline (Apple-Mail style) — the part the downloader skips."""
    m = EmailMessage()
    m["From"] = from_h
    m["To"] = "me@example.com"
    m["Subject"] = subject
    m["Date"] = "Mon, 01 Jun 2026 10:00:00 +0000"
    m.set_content("see attachments")
    m.add_attachment(MINIMAL_PDF, maintype="application", subtype="pdf", filename="rechnung.pdf")
    m.add_attachment(MINIMAL_PDF + b"\n%INLINE\n", maintype="application", subtype="pdf",
                     filename="inline-beleg.pdf", disposition="inline")
    return m.as_bytes()


class FakeServer:
    """Models the subset of imaplib.IMAP4_SSL that main() exercises, returning one
    message on FETCH and recording every command issued (so a test can assert which
    destructive commands did or did not happen)."""

    def __init__(self, raw_message, caps=(), trash_lines=None):
        self._raw = raw_message
        self.capabilities = caps
        self._trash_lines = trash_lines if trash_lines is not None else [
            b'(\\HasNoChildren \\Trash) "/" "Trash"'
        ]
        self.calls = []

    # constructor signature used by main(): IMAP4_SSL(host, port, ssl_context=ctx)
    def select(self, mailbox, readonly=False):
        self.calls.append(("SELECT", (mailbox, readonly)))
        return "OK", [b"1"]

    def list(self):
        return "OK", self._trash_lines

    def uid(self, command, *args):
        self.calls.append((command, args))
        if command == "SEARCH":
            return "OK", [b"1"]
        if command == "FETCH":
            return "OK", [(b"1 (RFC822)", self._raw)]
        return "OK", [b""]

    def expunge(self):
        self.calls.append(("EXPUNGE-FULL", ()))
        return "OK", [b""]

    def logout(self):
        self.calls.append(("LOGOUT", ()))
        return "OK", [b""]

    # convenience for assertions
    def issued(self, command):
        return [c for c in self.calls if c[0] == command]


def _write_config(tmp_path, host="imap.example.com"):
    cfg = tmp_path / "config.local.md"
    cfg.write_text(
        "## Account\n"
        "- `account_email:` me@example.com\n"
        f"- `imap_host:` {host}\n"
        "- `imap_user:` me@example.com\n"
        "- `imap_mailbox:` INBOX\n"
    )
    return cfg


def _run_main(monkeypatch, tmp_path, raw, *, caps=(), delete_after=None, protected=None,
              extra_argv=None, host="imap.example.com"):
    """Drive fa.main() once against a FakeServer. Returns (rc, server)."""
    server = FakeServer(raw, caps=caps)
    monkeypatch.setattr(fa.imaplib, "IMAP4_SSL", lambda *a, **k: server)
    monkeypatch.setattr(fa, "imap_login", lambda *a, **k: "FAKE")
    monkeypatch.setattr(fa, "load_delete_after_senders", lambda: set(delete_after or set()))
    monkeypatch.setattr(fa, "load_protected_senders", lambda: set(protected or set()))

    target = tmp_path / "Rechnungen"
    target.mkdir(exist_ok=True)
    cfg = _write_config(tmp_path, host=host)
    argv = ["fetch-attachments.py", "--config", str(cfg), "--folder", str(target)]
    argv += extra_argv or []
    monkeypatch.setattr(fa.sys, "argv", argv)
    rc = fa.main()
    return rc, server, target


# --------------------------------------------------------------------------- #
# S1: a `key: value <!-- a | b | c -->` config line must be parsed as a value,
# not silently dropped as a markdown table row just because its inline comment
# contains a pipe. (The shipped config.example.md keeps such comments.)
# --------------------------------------------------------------------------- #
def test_config_value_with_pipe_in_inline_comment_is_parsed(tmp_path):
    cfg = tmp_path / "config.local.md"
    cfg.write_text(
        "## Options\n"
        "- `ocr:` yes <!-- no | auto | yes -->\n"
        "- `delete_mode:` copy-trash <!-- gmail-trash | move-trash | copy-trash | refuse -->\n"
        "- `schedule_enabled:` true <!-- true | false -->\n",
        encoding="utf-8",
    )
    parsed = fa.parse_config(cfg)
    assert parsed["ocr"] == "yes"
    assert parsed["delete_mode"] == "copy-trash"
    assert parsed["schedule_enabled"] == "true"


def test_config_real_table_row_still_maps_to_folder(tmp_path):
    # Guard: stripping comments before the table check must not break a genuine
    # `| type | folder |` markdown table row.
    cfg = tmp_path / "config.local.md"
    cfg.write_text(
        "## Folders\n"
        "| invoices | ~/Documents/Rechnungen/ |\n",
        encoding="utf-8",
    )
    parsed = fa.parse_config(cfg)
    assert parsed["folder_map"]["invoices"] == "~/Documents/Rechnungen/"


# --------------------------------------------------------------------------- #
# H2: the verify-before-trash gate is wired through main().
# --------------------------------------------------------------------------- #
def test_filed_delete_after_sender_is_trashed_reversibly(monkeypatch, tmp_path):
    raw = _make_raw("Shop <news@shop.de>", "rechnung.pdf", MINIMAL_PDF, "application", "pdf")
    rc, server, target = _run_main(monkeypatch, tmp_path, raw, delete_after={"news@shop.de"})
    # filed on disk ...
    pdfs = list(target.glob("*.pdf"))
    assert len(pdfs) == 1
    # ... and trashed via the reversible copy-trash mechanism (COPY to Trash + flag source)
    assert server.issued("COPY"), "a verified-filed delete-after sender must be trashed"
    store_deleted = [c for c in server.issued("STORE") if "\\Deleted" in c[1]]
    assert store_deleted
    # copy-trash without UIDPLUS must NOT expunge (the copy is safe in Trash)
    assert server.issued("EXPUNGE") == [] and server.issued("EXPUNGE-FULL") == []


def test_filing_failure_issues_no_trash_command(monkeypatch, tmp_path):
    # Unsupported attachment type -> nothing is filed -> msg_filed stays False.
    # If main() ever trashed a delete-after sender without verified filing, COPY/MOVE/STORE
    # would appear here. This is the test that fails if the trash_eligible gate is removed.
    raw = _make_raw("Shop <news@shop.de>", "note.txt", b"hello", "text", "plain")
    rc, server, target = _run_main(monkeypatch, tmp_path, raw, delete_after={"news@shop.de"})
    assert list(target.glob("*")) == []                      # nothing filed
    assert server.issued("COPY") == []
    assert server.issued("MOVE") == []
    assert [c for c in server.issued("STORE") if "\\Deleted" in c[1]] == []
    assert server.issued("EXPUNGE") == [] and server.issued("EXPUNGE-FULL") == []


def test_mail_with_unfiled_inline_attachment_is_never_trashed(monkeypatch, tmp_path):
    # Data-loss guard: a delete-after mail whose inline PDF is skipped (never written to
    # disk) must NOT be trashed, even though a SIBLING attachment filed fine. Otherwise the
    # only copy of the inline document would go to Trash. This test fails if the
    # `msg_has_unfiled` guard is removed (the mail would then be trashed).
    raw = _make_raw_mixed_inline("Shop <news@shop.de>")
    rc, server, target = _run_main(monkeypatch, tmp_path, raw, delete_after={"news@shop.de"})
    # the normal attachment IS filed ...
    assert len(list(target.glob("*.pdf"))) == 1
    # ... but the mail is KEPT because the inline part was never filed
    assert server.issued("COPY") == []
    assert server.issued("MOVE") == []
    assert [c for c in server.issued("STORE") if "\\Deleted" in c[1]] == []
    assert server.issued("EXPUNGE") == [] and server.issued("EXPUNGE-FULL") == []


def test_protected_sender_not_trashed_through_main(monkeypatch, tmp_path):
    # Sender on BOTH lists: filed, but protection wins -> never trashed.
    raw = _make_raw("Kanzlei <berater@kanzlei.de>", "rechnung.pdf", MINIMAL_PDF, "application", "pdf")
    rc, server, target = _run_main(monkeypatch, tmp_path, raw,
                                   delete_after={"berater@kanzlei.de"},
                                   protected={"berater@kanzlei.de"})
    assert len(list(target.glob("*.pdf"))) == 1              # filed
    assert server.issued("COPY") == [] and server.issued("MOVE") == []


# --------------------------------------------------------------------------- #
# H3: re-runs do not pile up duplicate files (dedup wired through main()).
# --------------------------------------------------------------------------- #
def test_rerun_does_not_duplicate_file(monkeypatch, tmp_path):
    raw = _make_raw("Shop <news@shop.de>", "rechnung.pdf", MINIMAL_PDF, "application", "pdf")
    _run_main(monkeypatch, tmp_path, raw, delete_after={"news@shop.de"})
    rc, server, target = _run_main(monkeypatch, tmp_path, raw, delete_after={"news@shop.de"})
    assert len(list(target.glob("*.pdf"))) == 1              # still exactly one, not _v1


# --------------------------------------------------------------------------- #
# S2: ocr=yes rewrites the filed PDF in place, so its on-disk bytes diverge from
# the original payload. The pre-OCR payload-md5 sidecar must keep de-dup stable,
# so a re-run does NOT pile up _vN duplicates (and does not re-file/re-trash).
# --------------------------------------------------------------------------- #
def test_rerun_with_ocr_inplace_rewrite_does_not_duplicate(monkeypatch, tmp_path):
    def _fake_ocr(path, lang="deu+eng"):
        # simulate OCR adding a text layer: the on-disk bytes change (no longer == payload).
        path.write_bytes(path.read_bytes() + b"\n%OCR-TEXT-LAYER-ADDED\n")
        return True, "ok"

    monkeypatch.setattr(fa, "ocr_pdf", _fake_ocr)
    raw = _make_raw("Shop <news@shop.de>", "rechnung.pdf", MINIMAL_PDF, "application", "pdf")
    # run 1: file + OCR-rewrite in place
    _run_main(monkeypatch, tmp_path, raw, extra_argv=["--ocr", "yes", "--no-trash"])
    pdfs = list(tmp_path.glob("Rechnungen/*.pdf"))
    assert len(pdfs) == 1
    assert pdfs[0].read_bytes() != MINIMAL_PDF               # OCR really rewrote the bytes
    # run 2: same payload — must de-dup against the pre-OCR sidecar, no _v1 copy
    rc, server, target = _run_main(monkeypatch, tmp_path, raw,
                                   extra_argv=["--ocr", "yes", "--no-trash"])
    assert len(list(target.glob("*.pdf"))) == 1              # still exactly one, not _v1


# --------------------------------------------------------------------------- #
# S3: in --dry-run, a delete-after sender on a server with NO recoverable Trash
# (refuse mode) must be previewed as "would keep", NOT "would trash" — the
# preview has to predict the real run, which KEEPS such mail.
# --------------------------------------------------------------------------- #
def test_dry_run_previews_keep_when_no_reversible_trash(monkeypatch, tmp_path, capsys):
    raw = _make_raw("Shop <news@shop.de>", "rechnung.pdf", MINIMAL_PDF, "application", "pdf")
    # no Trash folder advertised + no UIDPLUS -> probe_delete_mode picks 'refuse'.
    server = FakeServer(raw, caps=(), trash_lines=[b'(\\HasNoChildren) "/" "INBOX"'])
    monkeypatch.setattr(fa.imaplib, "IMAP4_SSL", lambda *a, **k: server)
    monkeypatch.setattr(fa, "imap_login", lambda *a, **k: "FAKE")
    monkeypatch.setattr(fa, "load_delete_after_senders", lambda: {"news@shop.de"})
    monkeypatch.setattr(fa, "load_protected_senders", lambda: set())
    target = tmp_path / "R"
    target.mkdir()
    cfg = _write_config(tmp_path)
    monkeypatch.setattr(fa.sys, "argv",
                        ["x", "--config", str(cfg), "--folder", str(target), "--dry-run"])
    fa.main()
    out = capsys.readouterr().out
    assert "would keep" in out                               # accurate prediction
    assert "would trash" not in out                          # never a false promise


# --------------------------------------------------------------------------- #
# Forced permanent path is wired: --force-expunge + no Trash folder + UIDPLUS -> expunge.
# --------------------------------------------------------------------------- #
def test_force_expunge_path_is_reachable_only_when_forced(monkeypatch, tmp_path):
    raw = _make_raw("Shop <news@shop.de>", "rechnung.pdf", MINIMAL_PDF, "application", "pdf")
    # no Trash folder advertised -> without --force-expunge the run must NOT delete
    server = FakeServer(raw, caps=("UIDPLUS",), trash_lines=[b'(\\HasNoChildren) "/" "INBOX"'])
    monkeypatch.setattr(fa.imaplib, "IMAP4_SSL", lambda *a, **k: server)
    monkeypatch.setattr(fa, "imap_login", lambda *a, **k: "FAKE")
    monkeypatch.setattr(fa, "load_delete_after_senders", lambda: {"news@shop.de"})
    monkeypatch.setattr(fa, "load_protected_senders", lambda: set())
    target = tmp_path / "R"
    target.mkdir()
    cfg = _write_config(tmp_path)
    monkeypatch.setattr(fa.sys, "argv",
                        ["x", "--config", str(cfg), "--folder", str(target)])
    fa.main()
    assert server.issued("EXPUNGE") == [] and server.issued("EXPUNGE-FULL") == []
    assert server.issued("STORE") == []                      # refuse mode: nothing flagged
