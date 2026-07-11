"""--gmail-id selector: fetch one mail by its Gmail message id.

WHY: the unattended run holds the Gmail hex id (from the Gmail MCP), but --message-id
does an RFC822 `HEADER Message-ID` IMAP search — a Gmail hex id matches NOTHING there.
The run used to hit 0 results and then grep this tool's own SOURCE every single run to
re-derive that fact (a recurring hang/prompt surface). --gmail-id searches by Gmail's
X-GM-MSGID (the hex id as a decimal), which is exactly what the run already has.

build_search is a pure function (no IMAP), so the criteria are asserted directly.
"""
import types

import pytest

import fetch_attachments as fa


def _args(**kw):
    base = dict(message_id=None, gmail_id=None, sender=None,
                subject=None, since=None, before=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_gmail_id_builds_xgmmsgid_decimal_search():
    crit = fa.build_search(_args(gmail_id="1234567890abcdef"))
    assert crit == ["X-GM-MSGID", str(0x1234567890ABCDEF)]


def test_message_id_still_uses_rfc822_header():          # regression
    crit = fa.build_search(_args(message_id="<abc@mail>"))
    assert crit == ["HEADER", "Message-ID", fa._q("<abc@mail>")]


def test_gmail_id_combines_with_other_criteria():
    crit = fa.build_search(_args(gmail_id="a1b2c3", sender="x@y.z"))
    assert crit[0:2] == ["X-GM-MSGID", str(0xA1B2C3)]
    assert "FROM" in crit


# ---- CLI-level validation (argparse errors happen before any IMAP) --------- #

def _run(monkeypatch, argv):
    monkeypatch.setattr(fa.sys, "argv", ["fetch-attachments.py", *argv])
    return fa.main()


def test_gmail_id_and_message_id_are_mutually_exclusive(monkeypatch):
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, ["--gmail-id", "a1b2c3", "--message-id", "<x@y>", "--type", "receipts"])
    assert e.value.code == 2


def test_non_hex_gmail_id_is_rejected(monkeypatch):
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, ["--gmail-id", "zznot-hex", "--type", "receipts"])
    assert e.value.code == 2


def test_gmail_id_normalizes_0x_prefix_and_case():
    crit = fa.build_search(_args(gmail_id=fa._gmail_hex("0x1234567890ABCDEF")))
    assert crit == ["X-GM-MSGID", str(0x1234567890ABCDEF)]
