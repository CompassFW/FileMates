"""OAuth / login-path coverage (G-06, 2026-06-09).

Covers the previously untested credential paths WITHOUT network or browser:
  - oauth_access_token: token-file missing/corrupt -> loud RuntimeError (never fail-open);
    happy path + refresh-rejected path against a faked _post_token HTTP boundary.
  - imap_login: app-password precedence, the exact XOAUTH2 SASL string, and the
    no-credentials loud exit.
  - find_gmail_all_mail / select_mailbox: \\All-flag discovery + Gmail-only fallback.

HONESTLY NOT COVERED here: the interactive `--auth-setup` loopback flow (local HTTP
server + browser consent + state/CSRF check) — it needs a real browser round-trip and
remains manual-only. Its CSRF/state rejection is source-reviewed but not automated.
"""
import json

import pytest

import fetch_attachments as fa


# --------------------------------------------------------------------------- #
# oauth_access_token — file boundary + HTTP boundary
# --------------------------------------------------------------------------- #
def test_missing_token_file_raises_loud(monkeypatch, tmp_path):
    monkeypatch.setattr(fa, "OAUTH_TOKEN_FILE", tmp_path / "absent.json")
    with pytest.raises(RuntimeError, match="no OAuth token"):
        fa.oauth_access_token({})


def test_corrupt_token_file_raises_loud(monkeypatch, tmp_path):
    p = tmp_path / "tok.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(fa, "OAUTH_TOKEN_FILE", p)
    with pytest.raises(RuntimeError, match="unreadable"):
        fa.oauth_access_token({})


def test_token_file_without_refresh_key_raises_loud(monkeypatch, tmp_path):
    p = tmp_path / "tok.json"
    p.write_text(json.dumps({"something_else": "x"}), encoding="utf-8")
    monkeypatch.setattr(fa, "OAUTH_TOKEN_FILE", p)
    with pytest.raises(RuntimeError, match="unreadable"):
        fa.oauth_access_token({})


def test_refresh_happy_path_sends_refresh_grant(monkeypatch, tmp_path):
    p = tmp_path / "tok.json"
    p.write_text(json.dumps({"refresh_token": "RT-123"}), encoding="utf-8")
    monkeypatch.setattr(fa, "OAUTH_TOKEN_FILE", p)
    sent = {}

    def fake_post(params):
        sent.update(params)
        return {"access_token": "AT-456", "expires_in": 3599}

    monkeypatch.setattr(fa, "_post_token", fake_post)
    tok = fa.oauth_access_token({"oauth_client_id": "CID", "oauth_client_secret": "SEC"})
    assert tok == "AT-456"
    assert sent["grant_type"] == "refresh_token"
    assert sent["refresh_token"] == "RT-123"
    assert sent["client_id"] == "CID" and sent["client_secret"] == "SEC"


def test_refresh_rejected_raises_loud_never_fail_open(monkeypatch, tmp_path):
    # a revoked refresh token must raise (login then fails loud) — never return a
    # bogus token or silently continue.
    p = tmp_path / "tok.json"
    p.write_text(json.dumps({"refresh_token": "RT-revoked"}), encoding="utf-8")
    monkeypatch.setattr(fa, "OAUTH_TOKEN_FILE", p)
    monkeypatch.setattr(fa, "_post_token", lambda params: {"error": "invalid_grant"})
    with pytest.raises(RuntimeError, match="refresh failed"):
        fa.oauth_access_token({})


# --------------------------------------------------------------------------- #
# imap_login — method selection + the exact XOAUTH2 SASL string
# --------------------------------------------------------------------------- #
class _RecordingIMAP:
    def __init__(self):
        self.calls = []

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def authenticate(self, mech, authobject):
        self.calls.append(("authenticate", mech, authobject(None)))


def test_app_password_takes_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIL_APP_PASSWORD", "app-pw")
    # even WITH a token file present, the app password wins (cheaper, no HTTP).
    p = tmp_path / "tok.json"
    p.write_text(json.dumps({"refresh_token": "RT"}), encoding="utf-8")
    monkeypatch.setattr(fa, "OAUTH_TOKEN_FILE", p)
    # hermetic guard: if precedence ever broke, this test must FAIL here — never
    # fall through to a real HTTPS token request.
    monkeypatch.setattr(fa, "_post_token",
                        lambda params: pytest.fail("network hit — precedence broken"))
    imap = _RecordingIMAP()
    assert fa.imap_login(imap, {}, "u@example.com") == "app-password"
    assert imap.calls == [("login", "u@example.com", "app-pw")]


def test_xoauth2_sasl_string_is_exact(monkeypatch, tmp_path):
    monkeypatch.delenv("MAIL_APP_PASSWORD", raising=False)
    p = tmp_path / "tok.json"
    p.write_text(json.dumps({"refresh_token": "RT"}), encoding="utf-8")
    monkeypatch.setattr(fa, "OAUTH_TOKEN_FILE", p)
    monkeypatch.setattr(fa, "oauth_access_token", lambda cfg: "AT-789")
    imap = _RecordingIMAP()
    assert fa.imap_login(imap, {}, "u@example.com") == "XOAUTH2"
    mech_calls = [c for c in imap.calls if c[0] == "authenticate"]
    assert len(mech_calls) == 1
    _, mech, auth_bytes = mech_calls[0]
    assert mech == "XOAUTH2"
    # the SASL frame format Gmail requires — any drift here breaks login at runtime.
    assert auth_bytes == b"user=u@example.com\x01auth=Bearer AT-789\x01\x01"


def test_no_credentials_exits_loud(monkeypatch, tmp_path):
    monkeypatch.delenv("MAIL_APP_PASSWORD", raising=False)
    monkeypatch.setattr(fa, "OAUTH_TOKEN_FILE", tmp_path / "absent.json")
    with pytest.raises(SystemExit):
        fa.imap_login(_RecordingIMAP(), {}, "u@example.com")


# --------------------------------------------------------------------------- #
# Gmail All-Mail discovery + mailbox-select fallback
# --------------------------------------------------------------------------- #
class _ListIMAP:
    def __init__(self, list_lines, select_results):
        self._lines = list_lines
        self._select = dict(select_results)   # mailbox-name -> "OK"/"NO"
        self.selected = []

    def list(self):
        return "OK", self._lines

    def select(self, mailbox, readonly=False):
        name = mailbox.strip('"')
        self.selected.append(name)
        return self._select.get(name, "NO"), [b""]


def test_find_gmail_all_mail_locale_independent():
    imap = _ListIMAP([b'(\\HasNoChildren \\All) "/" "[Gmail]/Alle Nachrichten"'], {})
    assert fa.find_gmail_all_mail(imap) == "[Gmail]/Alle Nachrichten"


def test_find_gmail_all_mail_none_without_all_flag():
    imap = _ListIMAP([b'(\\HasNoChildren) "/" "INBOX"'], {})
    assert fa.find_gmail_all_mail(imap) is None


def test_select_mailbox_direct_hit():
    imap = _ListIMAP([], {"INBOX": "OK"})
    assert fa.select_mailbox(imap, "INBOX", "imap.gmx.net", readonly=True) == "INBOX"


def test_select_mailbox_gmail_falls_back_to_all_mail():
    imap = _ListIMAP([b'(\\All) "/" "[Gmail]/Alle Nachrichten"'],
                     {"[Gmail]/All Mail": "NO", "[Gmail]/Alle Nachrichten": "OK"})
    got = fa.select_mailbox(imap, "[Gmail]/All Mail", "imap.gmail.com", readonly=True)
    assert got == "[Gmail]/Alle Nachrichten"


def test_select_mailbox_non_gmail_fails_loud_no_fallback():
    imap = _ListIMAP([b'(\\All) "/" "Archive"'], {"INBOX.Sub": "NO"})
    with pytest.raises(RuntimeError, match="could not select"):
        fa.select_mailbox(imap, "INBOX.Sub", "imap.gmx.net", readonly=True)
    assert imap.selected == ["INBOX.Sub"]     # no blind fallback on non-Gmail hosts
