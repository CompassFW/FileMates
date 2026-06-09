"""Unit tests for the date-from-PDF feature (documented in fetch-attachments.py).

These exercise the TEXT-level parsing (`parse_doc_date`) directly with text
strings — no real PDFs, no pypdf, no pdftotext — so they cover the documented
behavior without any third-party dependency. The keyword sets come from the
code's own config resolver (`resolve_date_keywords`), so the tests track the
keywords the code actually uses rather than hard-coded copies.

`parse_doc_date(text, source, keywords)` returns `(datetime, keyword)` or
`(None, None)`. It matches a keyword on a word boundary, then looks for a date
on the keyword's own line first and the next line second (label-above-value
table layouts); for the `leistung` source (a service period "X - Y") the later
date wins.

Stdlib + pytest only.
"""
from datetime import datetime

import pytest

import fetch_attachments as fa


@pytest.fixture(scope="module")
def kw():
    # The real keyword config the downloader runs with (defaults, no overrides).
    return fa.resolve_date_keywords({})


# --------------------------------------------------------------------------- #
# Date on the SAME line as the keyword.
# --------------------------------------------------------------------------- #
def test_date_same_line_german_abbuchung(kw):
    d, k = fa.parse_doc_date("Abbuchung am 03.05.2026", "abbuchung", kw)
    assert d == datetime(2026, 5, 3)
    assert k == "abbuchung am"


def test_date_same_line_iso_format(kw):
    # "date paid" is an abbuchung keyword; ISO yyyy-mm-dd is supported.
    d, k = fa.parse_doc_date("date paid 2026-05-03", "abbuchung", kw)
    assert d == datetime(2026, 5, 3)
    assert k == "date paid"


def test_date_same_line_invoice(kw):
    d, k = fa.parse_doc_date("Rechnungsdatum: 12.04.2026", "rechnung", kw)
    assert d == datetime(2026, 4, 12)
    assert k == "rechnungsdatum"


# --------------------------------------------------------------------------- #
# Label ABOVE the value (table layout: keyword on its line, date on the next).
# --------------------------------------------------------------------------- #
def test_label_above_value_next_line(kw):
    d, k = fa.parse_doc_date("Rechnungsdatum\n12.04.2026", "rechnung", kw)
    assert d == datetime(2026, 4, 12)
    assert k == "rechnungsdatum"


def test_label_above_value_does_not_reach_two_lines_down(kw):
    # Only the keyword line + the immediately following line are scanned. A date
    # two lines below the keyword must NOT be picked up (guards against grabbing
    # an unrelated later date).
    d, k = fa.parse_doc_date("Rechnungsdatum\n\n12.04.2026", "rechnung", kw)
    assert (d, k) == (None, None)


# --------------------------------------------------------------------------- #
# Service-period END date wins for the 'leistung' source.
# --------------------------------------------------------------------------- #
def test_leistung_period_returns_end_date(kw):
    d, k = fa.parse_doc_date("Leistungszeitraum 01.01.2026 - 31.01.2026", "leistung", kw)
    assert d == datetime(2026, 1, 31)        # later date of the period wins
    assert k == "leistungszeitraum"


def test_non_leistung_period_returns_first_date(kw):
    # Contrast: for a non-leistung source the FIRST date on the line wins, so the
    # "later date wins" rule is provably specific to leistung.
    d, _ = fa.parse_doc_date("Rechnung vom 01.01.2026 bis 31.01.2026", "rechnung", kw)
    assert d == datetime(2026, 1, 1)


# --------------------------------------------------------------------------- #
# Word-boundary guard: a keyword embedded in a larger word must NOT match.
# --------------------------------------------------------------------------- #
def test_word_boundary_keyword_embedded_in_other_word_no_match(kw):
    # "leistung vom" must not fire inside "Gesamtleistung vom" (no \b before it).
    assert fa.parse_doc_date("Gesamtleistung vom 09.09.2026", "leistung", kw) == (None, None)


def test_word_boundary_invoice_keyword_embedded_no_match(kw):
    # "belegdatum" embedded inside "Vorbelegdatum" must not match.
    assert fa.parse_doc_date("Vorbelegdatum 09.09.2026", "rechnung", kw) == (None, None)


# --------------------------------------------------------------------------- #
# No keyword / unparseable input -> (None, None), never a crash.
# --------------------------------------------------------------------------- #
def test_no_keyword_in_text_returns_none(kw):
    assert fa.parse_doc_date("Irgendein Text voellig ohne Datum", "rechnung", kw) == (None, None)


def test_keyword_present_but_no_date_returns_none(kw):
    assert fa.parse_doc_date("Rechnungsdatum siehe Anhang", "rechnung", kw) == (None, None)


def test_empty_text_returns_none(kw):
    assert fa.parse_doc_date("", "rechnung", kw) == (None, None)


def test_unknown_source_returns_none(kw):
    # A source with no keyword list must not crash and must find nothing.
    assert fa.parse_doc_date("Rechnungsdatum 12.04.2026", "does-not-exist", kw) == (None, None)


def test_impossible_calendar_date_is_ignored(kw):
    # 31.02 is not a real date; the parser must skip it rather than raise.
    assert fa.parse_doc_date("Rechnungsdatum 31.02.2026", "rechnung", kw) == (None, None)


# --------------------------------------------------------------------------- #
# Keyword config resolver: overrides are honored, defaults otherwise.
# --------------------------------------------------------------------------- #
def test_resolve_date_keywords_uses_defaults_when_no_override():
    out = fa.resolve_date_keywords({})
    assert out["abbuchung"] == list(fa.DEFAULT_DATE_KEYWORDS["abbuchung"])


def test_resolve_date_keywords_applies_config_override():
    out = fa.resolve_date_keywords({"date_keywords_rechnung": "Zahltag; Stichtag"})
    assert out["rechnung"] == ["zahltag", "stichtag"]
    # Other sources keep their defaults.
    assert out["abbuchung"] == list(fa.DEFAULT_DATE_KEYWORDS["abbuchung"])


def test_overridden_keyword_drives_parsing(kw):
    # End-to-end of the override seam: a custom keyword actually parses a date.
    custom = fa.resolve_date_keywords({"date_keywords_rechnung": "Stichtag"})
    d, k = fa.parse_doc_date("Stichtag 07.07.2026", "rechnung", custom)
    assert d == datetime(2026, 7, 7)
    assert k == "stichtag"
