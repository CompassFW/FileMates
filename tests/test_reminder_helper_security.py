"""SECURITY-BOUNDARY suite for the FileMates reminder-helper (dev-review, RED).

This file pins the injection contract the whole safety case rests on: ANY value
that reaches a generated AppleScript (a reminder title/notes, a list name, a due
date) must be unable to break out of its string literal or inject a second
AppleScript statement, and any garbage due-date must be rejected before it can
reach the script. It also pins the list_reminders embedded-separator robustness
(SEC-2): a reminder whose body itself contains a field-separator char must still
parse with the correct `completed` flag and an intact body — no field shift.

Boundary feature (Beat 0 = boundary): these tests cross into AppleScript
generation. They are observed through the runner's OWN script-emit path via a
`_run` override seam (capture, don't exec) — NOT a hand-built harness — so they
prove the EMITTED, post-sanitize script, which is exactly where a breakout would
live. No production code is modified.

These tests FAIL against the current code for the right reason:
  * SEC-1 create_reminder breakout: _as_quote currently STRIPS the raw newline, so
    the malicious-title test passes today *for the newline vector* — but the
    line-join change (S1) introduces a per-line path, and these tests pin that the
    post-change emitted script STILL contains exactly one `make new reminder`
    statement and no `do shell script`. Where current behaviour already holds the
    invariant, the test is a regression lock (stays green) — flagged inline.
  * SEC-2: an embedded separator in the body currently shifts fields (body
    truncated, completed mis-read) -> these FAIL now and turn green only once the
    coder makes the body the trailing field with a capped split.
"""
import re as _re

import reminder_helper as rh

LIST = "Email-Tasks"
US, RS = chr(0x1F), chr(0x1E)
NL = chr(0x0A)


class _CapturingRunner(rh.OsascriptRunner):
    """Real OsascriptRunner with the _run choke-point overridden to capture the
    emitted AppleScript (and optionally return a canned raw for list_reminders),
    so security assertions observe the POST-sanitize emitted script, never a fake."""

    def __init__(self, raw=""):
        self.scripts = []
        self._raw = raw

    def _run(self, script):                          # capture, don't exec
        self.scripts.append(script)
        return self._raw


def _code_outside_string_literals(script: str) -> str:
    """Return the script with every double-quoted AppleScript string literal removed,
    so a payload that is safely CONFINED inside a literal (escaped quotes, stripped
    newlines) does not register — only a genuine breakout into executable CODE does.

    A real breakout requires an UNescaped `"` to close the literal early, leaving the
    payload as bare code. We walk the string honouring `\\"` and `\\\\` escapes; if the
    sanitizer did its job, the whole payload stays inside a literal and is stripped here.
    """
    out = []
    i, n, in_str = 0, len(script), False
    while i < n:
        ch = script[i]
        if in_str:
            if ch == "\\" and i + 1 < n:             # an escaped char stays inside the literal
                i += 2
                continue
            if ch == '"':                            # an UNescaped quote closes the literal
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# =========================================================================== #
# SEC-1 — the _as_quote injection contract (the single safety boundary).
# =========================================================================== #
def test_sec1_as_quote_neutralises_quote_and_newline_into_one_literal():
    # A value carrying a double-quote + a newline must come back as ONE safe
    # AppleScript string literal: no RAW " (only escaped \"), and no raw newline
    # that could terminate/append a statement.
    out = rh._as_quote('"' + NL + ' x')
    # exactly one opening and one closing literal quote -> a single literal:
    assert out.startswith('"') and out.endswith('"')
    inner = out[1:-1]
    # no UNescaped double-quote survives inside the literal:
    assert '\\"' in inner                       # the malicious quote was escaped
    assert _re.search(r'(?<!\\)"', inner) is None, f"raw unescaped quote in {out!r}"
    # no raw newline can sit inside an AppleScript string literal:
    assert NL not in out, f"raw newline survived into literal: {out!r}"


def test_sec1_as_quote_strips_us_rs_and_newline_control_chars():
    # US (0x1f) / RS (0x1e) / newline must be stripped from any interpolated value
    # (they are the list_reminders field separators AND statement terminators).
    out = rh._as_quote(f"a{US}b{RS}c{NL}d")
    assert US not in out
    assert RS not in out
    assert NL not in out
    assert out == '"abcd"'                      # control chars dropped, text intact


def test_sec1_as_quote_preserves_german_umlauts():
    # Regression lock: legitimate German text must pass through unharmed (the
    # sanitizer must not over-strip non-ASCII).
    assert rh._as_quote("Rückmeldung Steuerbüro nötig") == '"Rückmeldung Steuerbüro nötig"'


def test_sec1_parse_iso_due_rejects_garbage_suffixed_injection():
    # A due-date string carrying an AppleScript breakout suffix must be REJECTED
    # (-> None) so it can never reach the generated script.
    assert rh._parse_iso_due('2026-01-01"; do shell script "x') is None


def test_sec1_parse_iso_due_rejects_impossible_date():
    # An impossible calendar date is garbage -> None (never a phantom/garbled date).
    assert rh._parse_iso_due("2026-13-99") is None


def test_sec1_parse_iso_due_accepts_clean_iso():
    # Regression lock: a clean ISO date parses (so the positive path still works).
    parsed = rh._parse_iso_due("2026-06-30")
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day) == (2026, 6, 30)


def test_sec1_create_reminder_malicious_title_emits_no_breakout():
    # THE breakout test. A title carrying a quote + newline + a `do shell script`
    # payload must NOT produce any extra AppleScript statement: the emitted script
    # contains EXACTLY ONE `make new reminder` and NO `do shell script` line, and
    # the payload is confined inside the name:"..." literal (escaped/stripped).
    malicious = '"' + NL + 'do shell script "touch /tmp/x"'
    runner = _CapturingRunner()
    runner.create_reminder(LIST, malicious, body="recap\n[gmail:abc123]")
    assert len(runner.scripts) == 1
    script = runner.scripts[0]
    # exactly one reminder-creation statement, never a second injected statement:
    assert script.count("make new reminder") == 1
    # the payload must stay CONFINED inside the name:"..." literal — i.e. no
    # `do shell script` survives in the executable code OUTSIDE any string literal
    # (it may appear as inert escaped text inside the quoted title, which is safe).
    code = _code_outside_string_literals(script)
    assert "do shell script" not in code, f"breakout into code: {code!r}"
    assert "make new reminder" in code               # the intended statement is still real code
    # and no raw newline split the title into a second physical statement-line:
    assert NL + "do shell script" not in script


def test_sec1_create_reminder_malicious_body_emits_no_breakout():
    # A malicious NOTES/body (the most attacker-controllable field once mails are
    # parsed) must likewise not break out: one `make new reminder`, no shell script,
    # even with the S1 line-preserving emit in place.
    malicious_body = 'line1' + NL + '"; do shell script "rm -rf ~"' + NL + '[gmail:abc123]'
    runner = _CapturingRunner()
    runner.create_reminder(LIST, "Harmloser Titel", body=malicious_body)
    script = runner.scripts[0]
    assert script.count("make new reminder") == 1
    # the payload stays confined inside the body:"..." literal -> no executable
    # `do shell script` outside any string literal (the genuine breakout condition).
    code = _code_outside_string_literals(script)
    assert "do shell script" not in code, f"breakout into code: {code!r}"


# =========================================================================== #
# SEC-2 — list_reminders embedded-separator robustness (body is trailing field,
# capped split). A reminder whose body contains a separator char must still parse
# with the correct `completed` flag and the body intact (no field shift).
# Contract pinned in the PLANNED emit order (body trailing) so it fails today
# (body is the middle field now -> embedded separator shifts the parse).
# =========================================================================== #
def test_sec2_body_with_embedded_separator_keeps_completed_flag_and_body():
    # The coder reorders emitted fields so the BODY is the trailing field and uses a
    # capped split (so any separator inside the body is kept as part of the body).
    # Planned per-record layout: name<US>completed<US>body<RS> with split(US, 2).
    # A body containing a US must then parse with completed=True and body intact.
    body_with_sep = f"recap{US}with-embedded-separator{NL}[gmail:abc123]"
    raw = "MyTask" + US + "true" + US + body_with_sep + RS
    runner = _CapturingRunner(raw=raw)
    rems = runner.list_reminders(LIST)
    assert len(rems) == 1
    rem = rems[0]
    assert rem.name == "MyTask"
    assert rem.completed is True                 # flag NOT mis-read despite the embedded sep
    assert rem.body == body_with_sep             # body intact, no field shift / truncation


def test_sec2_body_with_separator_does_not_drop_the_reminder():
    # An open reminder whose body carries a separator must still appear (not dropped
    # by a `len(fields) < 3` guard after a shift) and keep completed=False.
    body_with_sep = f"a{US}b"
    raw = "Open" + US + "false" + US + body_with_sep + RS
    runner = _CapturingRunner(raw=raw)
    rems = runner.list_reminders(LIST)
    assert len(rems) == 1
    assert rems[0].completed is False
    assert rems[0].body == body_with_sep


def test_sec2_multiple_records_still_split_on_record_separator():
    # Regression lock: the record separator (RS) still delimits reminders even when
    # bodies contain the field separator (US) — two reminders parse as two, with the
    # right completed flags and intact bodies.
    r1 = "T1" + US + "true" + US + f"body1{US}x" + RS
    r2 = "T2" + US + "false" + US + "body2" + RS
    runner = _CapturingRunner(raw=r1 + r2)
    rems = runner.list_reminders(LIST)
    assert len(rems) == 2
    assert (rems[0].name, rems[0].completed, rems[0].body) == ("T1", True, f"body1{US}x")
    assert (rems[1].name, rems[1].completed, rems[1].body) == ("T2", False, "body2")
