"""Name-INDEPENDENT byte de-dup in the downloader.

Real recurring bug (monthly payroll "Lohnauswertung"): the same mail is re-processed
across runs, but the de-dup only scanned files matching the TARGET filename (`out.stem`
+ `_vN`). When the chosen name drifts between runs (e.g. the same payroll list labelled
by a different provider name), the scan looks under the new name, misses the already-filed
copy, and files a duplicate.

Fix: de-dup on the stable payload fingerprint recorded in the `.<name>.payload-md5`
sidecars present on every tool-filed document — so an identical attachment already filed
in the target folder under ANY name is recognised and skipped. Legacy files without a
sidecar still de-dup by name (unchanged).
"""
from email.message import EmailMessage

import fetch_attachments as fa
import hashlib

PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<<>>\n%%EOF\npayroll-june"


def _make_raw(filename="lohn.pdf"):
    m = EmailMessage()
    m["From"] = "Steuerkanzlei Muster <advisor@example.com>"
    m["To"] = "me@example.com"
    m["Subject"] = "Fw: Lohnauswertung"
    m["Date"] = "Sun, 14 Jun 2026 10:00:00 +0000"
    m.set_content("anbei")
    m.add_attachment(PDF, maintype="application", subtype="pdf", filename=filename)
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


def _run(monkeypatch, tmp_path, target, extra):
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
    monkeypatch.setattr(fa.sys, "argv",
                        ["x", "--config", str(cfg), "--folder", str(target), "--no-trash"] + extra)
    fa.main()


def test_identical_payload_already_filed_under_a_different_name_is_not_reduplicated(monkeypatch, tmp_path):
    target = tmp_path / "Gehaltsabrechnungen"
    target.mkdir()
    # Pre-existing filed copy from a PRIOR run, under the provider-A name, WITH its payload sidecar.
    prior = target / "ProviderA_Payroll_14-06-2026.pdf"
    prior.write_bytes(PDF)
    fa.write_payload_md5(prior, hashlib.md5(PDF).hexdigest())

    # This run names the SAME attachment differently — drifted name, same bytes.
    _run(monkeypatch, tmp_path, target,
         ["--type", "receipts", "--name-sender", "Steuerkanzlei", "--name-type", "Lohnauswertung"])

    # No duplicate created under the new name; the prior file is the only one.
    assert [p.name for p in target.glob("*.pdf")] == ["ProviderA_Payroll_14-06-2026.pdf"]


def test_distinct_payload_still_files_even_if_a_sidecar_exists(monkeypatch, tmp_path):
    # Guard against over-dedup: a DIFFERENT document must still file, not be swallowed
    # just because some other file in the folder has a sidecar.
    target = tmp_path / "Gehaltsabrechnungen"
    target.mkdir()
    other = target / "Something_Else.pdf"
    other.write_bytes(b"%PDF-1.4 totally different bytes")
    fa.write_payload_md5(other, hashlib.md5(b"%PDF-1.4 totally different bytes").hexdigest())

    _run(monkeypatch, tmp_path, target,
         ["--type", "receipts", "--name-sender", "Steuerkanzlei", "--name-type", "Lohnauswertung"])

    names = sorted(p.name for p in target.glob("*.pdf"))
    assert "Steuerkanzlei_Lohnauswertung_14-06-2026.pdf" in names   # the new doc IS filed
    assert "Something_Else.pdf" in names
