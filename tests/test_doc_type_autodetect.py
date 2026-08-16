"""Per-attachment document type detection (Rechnung vs Beleg) + loud name collisions.

Real regression this guards (2026-08-10): a vendor mail carries BOTH the invoice and the
paid receipt as two PDFs. `--name-type` is a single per-RUN value, so both documents were
rendered to the same name and the second silently became `…_v1.pdf`. Four such pairs had
accumulated unnoticed across three months, and the run report mis-read them as duplicate
downloads.

Two fixes, both tested here:
  1. `--name-type auto` derives the German document type PER ATTACHMENT from the PDF text,
     so invoice + receipt get distinct, convention-correct names.
  2. Any `_vN` fallback that still happens is reported in its own summary block instead of
     passing silently — a collision must be visible in the run report.
"""
from email.message import EmailMessage

import fetch_attachments as fa

INVOICE_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<<inv>>\n%%EOF\n"
RECEIPT_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<<rec>>\n%%EOF\n"

INVOICE_TEXT = """Invoice
Invoice number ZZ-0015
Date of issue August 7, 2026
Anthropic, PBC
EUR 214.20 due August 7, 2026
Amount due 214.20
"""

RECEIPT_TEXT = """Receipt
Invoice number ZZ-0015
Receipt number 2249-1931-8439
Date paid August 7, 2026
EUR 214.20 paid on August 7, 2026
Amount paid 214.20
Payment history
"""


# --------------------------------------------------------------------------- unit


def test_receipt_text_is_beleg():
    assert fa.detect_doc_type(RECEIPT_TEXT) == "Beleg"


def test_invoice_text_is_rechnung():
    assert fa.detect_doc_type(INVOICE_TEXT) == "Rechnung"


def test_receipt_wins_over_the_invoice_number_it_quotes():
    # THE regression: a receipt always cites its invoice number, so plain "invoice"
    # evidence must never outrank the receipt markers.
    assert "Invoice number" in RECEIPT_TEXT
    assert fa.detect_doc_type(RECEIPT_TEXT) == "Beleg"


def test_german_invoice_is_rechnung():
    text = ("Ihre EnBW mobility+ Rechnung fuer Deutschland im Juni 2026\n"
            "Rechnungs-Nr. 180 171 507 769\nRechnungsendbetrag 31,06 EUR\n")
    assert fa.detect_doc_type(text) == "Rechnung"


def test_paid_invoice_stays_rechnung():
    # Squarespace-style: an INVOICE that reports it was already settled ("Bezahlt 9,52 €").
    # A bare "bezahlt" must not flip the type — otherwise every filed invoice renames itself.
    text = ("Rechnung\n#243463025\nBerechnet am Montag, 20. Juli 2026\n"
            "Zwischensumme 8,00 EUR\nFaellig 0,00 EUR\nBezahlt 9,52 EUR\n")
    assert fa.detect_doc_type(text) == "Rechnung"


def test_german_receipt_is_beleg():
    text = "Zahlungsbeleg\nQuittung Nr. 5\nBetrag bezahlt am 09.08.2026: 22,00 EUR\n"
    assert fa.detect_doc_type(text) == "Beleg"


def test_unrelated_text_is_undecided():
    assert fa.detect_doc_type("Reisekostenrichtlinie und Anfahrtsskizze") is None


def test_empty_text_is_undecided():
    assert fa.detect_doc_type("") is None
    assert fa.detect_doc_type(None) is None


# ------------------------------------------------------- heading beats body prose

# Verbatim shape of a real ElevenLabs invoice (2026-08-09): the heading says Invoice,
# but a VAT footnote at the bottom contains the phrase "paid on". Body-only detection
# read that as a receipt, so invoice and receipt collided on one name every month.
INVOICE_WITH_VAT_FOOTNOTE = """Invoice
Invoice number 7E5B5F0-0005
Date of issue August 9, 2026
$22.00 USD due August 9, 2026
Creator (per subscription) 1 $22.00
Amount due $22.00 USD
[1] Tax to be paid on reverse charge basis
"""

RECEIPT_QUOTING_ITS_INVOICE = """Receipt
Invoice number 7E5B5F0-0005
Receipt number 2559-4010-2236
Date paid August 9, 2026
Amount paid $22.00
"""


def test_invoice_footnote_does_not_flip_to_beleg():
    # THE regression (2026-08-16): "Tax to be paid on reverse charge basis" is prose,
    # not proof of payment. The Invoice heading decides.
    assert "paid on" in INVOICE_WITH_VAT_FOOTNOTE
    assert fa.detect_doc_type(INVOICE_WITH_VAT_FOOTNOTE) == "Rechnung"


def test_heading_beats_the_invoice_number_a_receipt_quotes():
    assert fa.detect_doc_type(RECEIPT_QUOTING_ITS_INVOICE) == "Beleg"


def test_line_with_digits_is_not_a_heading():
    # "Invoice number 7E5B5F0-0005" must never count as a heading — otherwise a receipt
    # whose own title didn't survive text extraction would be filed as an invoice.
    text = "Invoice number 7E5B5F0-0005\nDate paid August 9, 2026\nAmount paid $22.00\n"
    assert fa.detect_doc_type(text) == "Beleg"


def test_german_heading_wins_over_paid_total():
    # Squarespace: heading "Rechnung", but the body reports it as settled.
    text = "Rechnung\n#243463025\nBerechnet am Montag, 20. Juli 2026\nBezahlt 9,52 EUR\n"
    assert fa.detect_doc_type(text) == "Rechnung"


def test_heading_only_scans_the_first_lines():
    # A heading is a heading because it stands at the TOP. A short, digit-free line
    # "Invoice" further down (a section title, a link label) is not — it must not
    # outrank the body evidence. Note the top of this document carries no heading, so
    # only the scan window keeps the stray word from deciding.
    text = ("Acme Audio Inc.\nKundin Erika Mustermann\nAbrechnung\nAmount paid $22.00\n"
            + "\n".join(f"Zeile {i}" for i in range(20)) + "\nInvoice\n")
    assert fa._heading_doc_type(text) is None          # nothing heading-like up top
    assert fa.detect_doc_type(text) == "Beleg"


def test_vat_footnote_is_no_receipt_evidence_even_without_a_heading():
    # Second line of defence: if the heading didn't survive text extraction (scan/OCR),
    # the body must still not read "Tax to be paid on reverse charge basis" as payment.
    # This pins the removal of "paid on" from the body markers — the heading stage alone
    # would hide a regression here.
    text = ("Eleven Labs Inc. 169 Madison Avenue #2484\n"
            "$22.00 USD due August 9, 2026\nAmount due $22.00 USD\n"
            "[1] Tax to be paid on reverse charge basis\n")
    assert fa._heading_doc_type(text) is None          # no heading in play
    assert fa.detect_doc_type(text) == "Rechnung"


def test_no_heading_falls_back_to_body_markers():
    # EnBW: the type only appears mid-page, so stage 2 has to carry it.
    text = ("Stadtwerke Musterstadt AG\nMusterallee 93\n12345 Musterstadt\n"
            "Erika Mustermann\nBeispielweg 1\nIhre Ladestrom-Rechnung\n"
            "Rechnungsendbetrag 91,43 EUR\n")
    assert fa.detect_doc_type(text) == "Rechnung"


# -------------------------------------------------------------------- integration


def _make_raw(attachments):
    m = EmailMessage()
    m["From"] = "Anthropic <invoice+statements@mail.anthropic.com>"
    m["To"] = "me@example.com"
    m["Subject"] = "Your receipt from Anthropic"
    m["Date"] = "Fri, 07 Aug 2026 10:00:00 +0000"
    m.set_content("see attachments")
    for payload, fname in attachments:
        m.add_attachment(payload, maintype="application", subtype="pdf", filename=fname)
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


TEXTS = {INVOICE_PDF: INVOICE_TEXT, RECEIPT_PDF: RECEIPT_TEXT}


def _run(monkeypatch, tmp_path, extra=None, attachments=None, texts=None):
    attachments = attachments or [(INVOICE_PDF, "invoice.pdf"), (RECEIPT_PDF, "receipt.pdf")]
    texts = TEXTS if texts is None else texts
    monkeypatch.setattr(fa.imaplib, "IMAP4_SSL",
                        lambda *a, **k: FakeServer(_make_raw(attachments)))
    monkeypatch.setattr(fa, "imap_login", lambda *a, **k: "FAKE")
    monkeypatch.setattr(fa, "load_delete_after_senders", lambda: set())
    monkeypatch.setattr(fa, "load_protected_senders", lambda: set())
    # seam: no real pdftotext in CI — the text layer is supplied per payload.
    monkeypatch.setattr(fa, "extract_pdf_text", lambda payload: texts.get(payload, ""))
    cfg = tmp_path / "config.local.md"
    cfg.write_text(
        "- `imap_host:` imap.example.com\n"
        "- `imap_user:` me@example.com\n"
        "- `naming_scheme:` <sender>_<type>_<TT-MM-JJJJ>\n",
        encoding="utf-8",
    )
    target = tmp_path / "R"
    target.mkdir(exist_ok=True)
    argv = ["x", "--config", str(cfg), "--folder", str(target), "--no-trash",
            "--name-sender", "Anthropic"] + (extra or [])
    monkeypatch.setattr(fa.sys, "argv", argv)
    rc = fa.main()
    return target, rc


def test_auto_splits_invoice_and_receipt(monkeypatch, tmp_path):
    target, rc = _run(monkeypatch, tmp_path, ["--type", "receipts", "--name-type", "auto"])
    assert sorted(p.name for p in target.glob("*.pdf")) == [
        "Anthropic_Beleg_07-08-2026.pdf",
        "Anthropic_Rechnung_07-08-2026.pdf",
    ]
    assert rc == 0


def test_auto_produces_no_v1_collision(monkeypatch, tmp_path):
    target, _ = _run(monkeypatch, tmp_path, ["--type", "receipts", "--name-type", "auto"])
    assert list(target.glob("*_v1*")) == []


def test_explicit_name_type_still_wins(monkeypatch, tmp_path):
    # backwards compatibility: a literal --name-type is used verbatim for every attachment.
    target, _ = _run(monkeypatch, tmp_path, ["--type", "receipts", "--name-type", "Beleg"])
    names = sorted(p.name for p in target.glob("*.pdf"))
    assert names == ["Anthropic_Beleg_07-08-2026.pdf", "Anthropic_Beleg_07-08-2026_v1.pdf"]


def test_collision_is_reported_not_silent(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, tmp_path, ["--type", "receipts", "--name-type", "Beleg"])
    out = capsys.readouterr().out
    assert "Name collisions" in out
    assert "Anthropic_Beleg_07-08-2026_v1.pdf" in out


def test_no_collision_block_when_names_are_distinct(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, tmp_path, ["--type", "receipts", "--name-type", "auto"])
    assert "Name collisions" not in capsys.readouterr().out


def test_auto_falls_back_to_type_when_undecidable(monkeypatch, tmp_path):
    target, _ = _run(monkeypatch, tmp_path, ["--type", "receipts", "--name-type", "auto"],
                     attachments=[(INVOICE_PDF, "anlage.pdf")],
                     texts={INVOICE_PDF: "Anfahrtsskizze zum Termin"})
    assert [p.name for p in target.glob("*.pdf")] == ["Anthropic_receipts_07-08-2026.pdf"]


def test_auto_dry_run_writes_nothing(monkeypatch, tmp_path):
    target, _ = _run(monkeypatch, tmp_path,
                     ["--type", "receipts", "--name-type", "auto", "--dry-run"])
    assert list(target.glob("*.pdf")) == []


def test_without_name_type_behaviour_unchanged(monkeypatch, tmp_path):
    # regression guard: the old default (English folder key as <type>) is untouched.
    target, _ = _run(monkeypatch, tmp_path, ["--type", "receipts"])
    names = sorted(p.name for p in target.glob("*.pdf"))
    assert names == ["Anthropic_receipts_07-08-2026.pdf",
                     "Anthropic_receipts_07-08-2026_v1.pdf"]
