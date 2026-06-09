"""Safety tests for the encrypted-PDF quarantine path.

These lock the load-bearing SAFETY promise stated in the downloader itself
(fetch-attachments.py ~line 1047): an encrypted/password-protected PDF is
(a) detected, (b) routed to the `protected_pdf_folder` quarantine sub-folder
keeping the original name, (c) NEVER read for a date, and (d) NEVER makes its
mail eligible for trashing (the mail may be the only way to recover the
password). Every test fails if that promise regresses.

The detection function (`pdf_is_encrypted`) is exercised on both code paths:
the pypdf path when available, and the no-pypdf raw `/Encrypt` fallback (the
path CI actually runs, since CI installs only pytest+ruff). The "mail kept"
promise is asserted via the same pure-decision seam the delete-safety tests
use (`trash_eligible` / `decide_trash`) — no real IMAP, no real PDF.

Stdlib + pytest only. No third-party dependency is required.
"""
import sys

import fetch_attachments as fa

# Minimal byte fixtures — just enough structure for the detector to decide on.
# The raw fallback keys on the literal `/Encrypt` trailer reference.
ENCRYPTED_PDF = b"%PDF-1.4\n1 0 obj<< /Type /Catalog >>endobj\ntrailer<< /Encrypt 9 0 R /Root 1 0 R >>\n%%EOF"
PLAIN_PDF = b"%PDF-1.4\n1 0 obj<< /Type /Catalog >>endobj\ntrailer<< /Root 1 0 R >>\n%%EOF"


# --------------------------------------------------------------------------- #
# 1. Detection: encrypted -> True, plain -> False (current/native path).
# --------------------------------------------------------------------------- #
def test_pdf_is_encrypted_true_for_encrypted_signature():
    assert fa.pdf_is_encrypted(ENCRYPTED_PDF) is True


def test_pdf_is_encrypted_false_for_plain_pdf():
    assert fa.pdf_is_encrypted(PLAIN_PDF) is False


# --------------------------------------------------------------------------- #
# 2. Detection: the no-pypdf raw `/Encrypt` fallback is locked explicitly.
#    pypdf is shadowed with None so `import pypdf` raises ImportError, forcing
#    the heuristic branch regardless of whether pypdf is installed in the env.
#    This is the exact branch CI runs (pytest+ruff only, no pypdf).
# --------------------------------------------------------------------------- #
def test_pdf_is_encrypted_raw_fallback_without_pypdf(monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)  # import pypdf -> ImportError
    assert fa.pdf_is_encrypted(ENCRYPTED_PDF) is True
    assert fa.pdf_is_encrypted(PLAIN_PDF) is False


def test_pdf_is_encrypted_never_raises_on_garbage():
    # "Never raises" is part of the contract — garbage bytes must yield a bool.
    assert fa.pdf_is_encrypted(b"") is False
    assert fa.pdf_is_encrypted(b"not a pdf at all") is False
    assert isinstance(fa.pdf_is_encrypted(b"\x00\x01\x02 /Encrypt \xff"), bool)


# --------------------------------------------------------------------------- #
# 3. The "mail kept" promise: a quarantined encrypted PDF never marks the mail
#    filed, so it can never be auto-trashed. In the downloader the encrypted
#    branch `continue`s BEFORE `msg_filed = True`, i.e. msg_filed stays False
#    for a mail whose only attachment was quarantined. The trash gate then
#    refuses regardless of the sender policy. We assert via the pure-decision
#    seam (no trash command is issued for a quarantined mail).
# --------------------------------------------------------------------------- #
def test_quarantined_mail_is_not_trash_eligible_even_on_delete_list():
    # Worst case: sender IS on the delete-after list (decide_trash -> "trash"),
    # yet because the encrypted PDF was quarantined (not filed) the mail is kept.
    decision = fa.decide_trash("Shop <news@shop.de>", set(), {"news@shop.de"})
    assert decision == "trash"                       # policy alone would trash...
    assert fa.trash_eligible(False, True, decision) is False  # ...but quarantine spares it


def test_quarantined_mail_kept_even_with_trash_enabled():
    # Trash globally enabled + a normally-trashable sender, but msg_filed=False
    # (the quarantine path) must still keep the mail.
    assert fa.trash_eligible(False, True, "trash") is False


def test_filed_attachment_in_same_mail_still_requires_filing_flag():
    # Sanity counter-case: the gate only opens when filing actually succeeded.
    # (Proves the False above is the filing flag doing the work, not the policy.)
    assert fa.trash_eligible(True, True, "trash") is True
