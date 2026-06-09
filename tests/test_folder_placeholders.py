"""Dated folder placeholders (2026-06-09).

The user's filing convention is month subfolders (e.g. `Belege 2026/06_Juni/`), but the
folder map could only point at static roots — so a scheduled run filed everything flat
into the root. Fix: folder paths may carry date placeholders, resolved PER ATTACHMENT
from the SAME date the filename uses (`doc_when`, i.e. the payment date when
date_source=abbuchung, else the mail date):

    <YYYY>      -> 2026
    <MM>        -> 06
    <MM_Monat>  -> 06_Juni   (German month names)

Strictness: a dated folder with NO resolvable date is never guessed — the attachment is
reported as a problem and the mail is kept (msg_has_unfiled guard: never trashed).
"""
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import fetch_attachments as fa

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<<>>\n%%EOF\n"


# --------------------------------------------------------------------------- #
# Pure: resolve_dated_folder(template, when)
# --------------------------------------------------------------------------- #
def test_no_placeholder_passes_through():
    p = Path("/x/Belege 2026")
    assert fa.resolve_dated_folder(p, datetime(2026, 6, 9)) == p


def test_yyyy_mm_and_month_name_resolve():
    out = fa.resolve_dated_folder(Path("/x/Belege <YYYY>/<MM_Monat>"), datetime(2026, 6, 9))
    assert out == Path("/x/Belege 2026/06_Juni")
    out2 = fa.resolve_dated_folder(Path("/x/<YYYY>-<MM>"), datetime(2026, 6, 9))
    assert out2 == Path("/x/2026-06")


def test_all_german_month_names():
    months = ["01_Januar", "02_Februar", "03_März", "04_April", "05_Mai", "06_Juni",
              "07_Juli", "08_August", "09_September", "10_Oktober", "11_November",
              "12_Dezember"]
    for m in range(1, 13):
        out = fa.resolve_dated_folder(Path("/x/<MM_Monat>"), datetime(2026, m, 1))
        assert out == Path(f"/x/{months[m - 1]}"), f"month {m}"


def test_placeholder_without_date_returns_none():
    # never guess a month — caller must keep the mail + report a problem.
    assert fa.resolve_dated_folder(Path("/x/<MM_Monat>"), None) is None


def test_no_placeholder_without_date_is_fine():
    p = Path("/x/static")
    assert fa.resolve_dated_folder(p, None) == p


def test_has_dated_placeholder():
    assert fa.has_dated_placeholder(Path("/x/Belege <YYYY>/<MM_Monat>"))
    assert fa.has_dated_placeholder(Path("/x/<MM>"))
    assert not fa.has_dated_placeholder(Path("/x/Belege 2026"))


def test_unknown_placeholders_detected():
    assert fa.unknown_placeholders(Path("/x/Belege <JJJJ>/<MM_Monat>")) == ["<JJJJ>"]
    assert fa.unknown_placeholders(Path("/x/Belege <YYYY>/<MM_Monat>")) == []
    assert fa.unknown_placeholders(Path("/x/static")) == []


def test_typoed_placeholder_refuses_even_with_mkdir(monkeypatch, tmp_path):
    # bogus-folder class: a typo like <JJJJ> must error loudly — with --mkdir it would
    # otherwise be CREATED as a literal '<JJJJ>' directory.
    import pytest
    monkeypatch.setattr(fa.imaplib, "IMAP4_SSL", lambda *a, **k: FakeServer(_make_raw()))
    monkeypatch.setattr(fa, "imap_login", lambda *a, **k: "FAKE")
    cfg = tmp_path / "config.local.md"
    cfg.write_text("- `imap_host:` h\n- `imap_user:` u\n", encoding="utf-8")
    monkeypatch.setattr(fa.sys, "argv",
                        ["x", "--config", str(cfg), "--mkdir",
                         "--folder", str(tmp_path / "Belege <JJJJ>" / "<MM_Monat>")])
    with pytest.raises(SystemExit):
        fa.main()
    assert list(tmp_path.glob("*JJJJ*")) == []            # nothing created


# --------------------------------------------------------------------------- #
# Integration through main() — FakeServer harness (mirrors test_main_integration).
# --------------------------------------------------------------------------- #
def _make_raw(filename="rechnung.pdf", date_h="Mon, 01 Jun 2026 10:00:00 +0000"):
    m = EmailMessage()
    m["From"] = "Shop <news@shop.de>"
    m["To"] = "me@example.com"
    m["Subject"] = "Rechnung"
    if date_h:
        m["Date"] = date_h
    m.set_content("see attachment")
    m.add_attachment(MINIMAL_PDF, maintype="application", subtype="pdf", filename=filename)
    return m.as_bytes()


class FakeServer:
    def __init__(self, raw):
        self._raw = raw
        self.capabilities = ()
        self.calls = []

    def select(self, mailbox, readonly=False):
        return "OK", [b"1"]

    def list(self):
        return "OK", [b'(\\HasNoChildren \\Trash) "/" "Trash"']

    def uid(self, command, *args):
        self.calls.append((command, args))
        if command == "SEARCH":
            return "OK", [b"1"]
        if command == "FETCH":
            return "OK", [(b"1 (RFC822)", self._raw)]
        return "OK", [b""]

    def logout(self):
        return "OK", [b""]

    def issued(self, command):
        return [c for c in self.calls if c[0] == command]


def _run(monkeypatch, tmp_path, raw, folder, extra=None, delete_after=None):
    server = FakeServer(raw)
    monkeypatch.setattr(fa.imaplib, "IMAP4_SSL", lambda *a, **k: server)
    monkeypatch.setattr(fa, "imap_login", lambda *a, **k: "FAKE")
    monkeypatch.setattr(fa, "load_delete_after_senders", lambda: set(delete_after or set()))
    monkeypatch.setattr(fa, "load_protected_senders", lambda: set())
    cfg = tmp_path / "config.local.md"
    cfg.write_text("- `imap_host:` imap.example.com\n- `imap_user:` me@example.com\n",
                   encoding="utf-8")
    argv = ["x", "--config", str(cfg), "--folder", folder, "--no-trash"] + (extra or [])
    monkeypatch.setattr(fa.sys, "argv", argv)
    rc = fa.main()
    return rc, server


def test_files_into_existing_month_subfolder(monkeypatch, tmp_path):
    # mail date 01 Jun 2026 -> <MM_Monat> = 06_Juni; the month dir exists -> file lands there.
    (tmp_path / "Belege" / "06_Juni").mkdir(parents=True)
    rc, _ = _run(monkeypatch, tmp_path, _make_raw(), str(tmp_path / "Belege" / "<MM_Monat>"))
    assert len(list((tmp_path / "Belege" / "06_Juni").glob("*.pdf"))) == 1
    assert list((tmp_path / "Belege").glob("*.pdf")) == []       # nothing flat in the root
    assert rc == 0                                               # clean run -> exit 0


def test_missing_month_folder_without_mkdir_files_nothing_and_keeps_mail(monkeypatch, tmp_path):
    # bogus-folder lesson, per attachment: missing dated target + no --mkdir -> nothing
    # written anywhere, and a delete-after mail is NOT trashed (unfiled guard).
    (tmp_path / "Belege").mkdir()
    rc, server = _run(monkeypatch, tmp_path, _make_raw(),
                      str(tmp_path / "Belege" / "<MM_Monat>"),
                      extra=[], delete_after={"news@shop.de"})
    assert list((tmp_path / "Belege").rglob("*.pdf")) == []
    assert server.issued("COPY") == [] and server.issued("MOVE") == []
    assert rc == 1                  # a problem run must exit 1 (record-run criterion)


def test_missing_month_folder_with_mkdir_creates_and_files(monkeypatch, tmp_path):
    (tmp_path / "Belege").mkdir()
    rc, _ = _run(monkeypatch, tmp_path, _make_raw(), str(tmp_path / "Belege" / "<MM_Monat>"),
                 extra=["--mkdir"])
    assert len(list((tmp_path / "Belege" / "06_Juni").glob("*.pdf"))) == 1
    assert rc == 0


def test_unparsable_mail_date_with_dated_folder_keeps_mail(monkeypatch, tmp_path):
    # no resolvable date + dated folder -> never guess: nothing filed, mail kept.
    (tmp_path / "Belege" / "06_Juni").mkdir(parents=True)
    raw = _make_raw(date_h=None)                                  # no Date header at all
    rc, server = _run(monkeypatch, tmp_path, raw,
                      str(tmp_path / "Belege" / "<MM_Monat>"),
                      delete_after={"news@shop.de"})
    assert list((tmp_path / "Belege").rglob("*.pdf")) == []
    assert server.issued("COPY") == [] and server.issued("MOVE") == []
    assert rc == 1                  # unfiled doc is a reported problem -> exit 1


def test_static_existing_folder_behaviour_unchanged(monkeypatch, tmp_path):
    # regression: a plain static --folder that EXISTS still works exactly as before.
    # (A MISSING static folder now surfaces per attachment instead of the old
    # "would file" preview — intentional: the preview must predict the real run.)
    (tmp_path / "R").mkdir()
    _run(monkeypatch, tmp_path, _make_raw(), str(tmp_path / "R"))
    assert len(list((tmp_path / "R").glob("*.pdf"))) == 1


def _make_raw_mixed_two_pdfs():
    """No Date header + two PDFs: payload A carries a parseable payment date (via the
    extract_pdf_text seam), payload B has no readable text -> doc_when=None for B."""
    m = EmailMessage()
    m["From"] = "Shop <news@shop.de>"
    m["To"] = "me@example.com"
    m["Subject"] = "Rechnung"
    m.set_content("see attachments")                       # NO Date header on purpose
    m.add_attachment(MINIMAL_PDF + b"%A", maintype="application", subtype="pdf",
                     filename="a.pdf")
    m.add_attachment(MINIMAL_PDF + b"%B", maintype="application", subtype="pdf",
                     filename="b.pdf")
    return m.as_bytes()


def test_dated_branch_unfiled_part_blocks_trash_and_pins_exit_codes(monkeypatch, tmp_path):
    # THE data-loss guard on the DATED branch (kills the mutant that drops
    # `msg_has_unfiled = True` in the t_err path): a delete-after mail where PDF A files
    # into the month folder but PDF B has NO resolvable date must NEVER be trashed —
    # WITHOUT --no-trash, so the trash gate is genuinely armed.
    (tmp_path / "Belege" / "06_Juni").mkdir(parents=True)
    monkeypatch.setattr(
        fa, "extract_pdf_text",
        lambda payload: "buchen wir am 05.06.2026" if b"%A" in payload else "")
    server = FakeServer(_make_raw_mixed_two_pdfs())
    monkeypatch.setattr(fa.imaplib, "IMAP4_SSL", lambda *a, **k: server)
    monkeypatch.setattr(fa, "imap_login", lambda *a, **k: "FAKE")
    monkeypatch.setattr(fa, "load_delete_after_senders", lambda: {"news@shop.de"})
    monkeypatch.setattr(fa, "load_protected_senders", lambda: set())
    cfg = tmp_path / "config.local.md"
    cfg.write_text(
        "- `imap_host:` imap.example.com\n- `imap_user:` me@example.com\n"
        "- `date_source:` abbuchung\n"
        "- `date_keywords_abbuchung:` buchen wir am\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(fa.sys, "argv",
                        ["x", "--config", str(cfg),
                         "--folder", str(tmp_path / "Belege" / "<MM_Monat>")])
    rc = fa.main()
    # PDF A is filed into the month folder ...
    assert len(list((tmp_path / "Belege" / "06_Juni").glob("*.pdf"))) == 1
    # ... but the mail is KEPT (PDF B never reached disk) — no trash command at all:
    assert server.issued("COPY") == []
    assert server.issued("MOVE") == []
    assert [c for c in server.issued("STORE") if "\\Deleted" in str(c[1])] == []
    # and the unfiled part is a real problem -> exit code 1 (the scheduled run's
    # record-run criterion reads exactly this).
    assert rc == 1
