# --------------------------------------------------------------------------- #
# Deterministic waiting-list matcher.
#
# WHY: the "waiting list" (open cases where the user waits on an EXTERNAL reply
# that does not arrive by mail — e.g. a refund visible only in the bank app) was
# matched by the LLM re-reading prose matchers every run. That drifted: a mail
# belonging to an open case got re-downloaded / re-reminded / re-asked, and once
# an exclusion ("NOT the monthly vendor invoice") was mis-applied, the monthly
# invoice was wrongly parked for weeks.
#
# This moves the KNOWN-signal part (known gmail-ids, known senders, exclusions)
# into a deterministic, testable function. The machine matchers live in an
# explicit `[[match ids=… senders=… exclude=…]]` token appended to each
# waiting-row in config.local.md; the human prose stays in front of it.
#
# The fuzzy "does this new mail look like it belongs" stays with the LLM (that is
# for PROPOSING new cases) — this function only catches what is already KNOWN.
#
# PII: all ids/senders/case names here are synthetic (no real production data).
# --------------------------------------------------------------------------- #
import reminder_helper as rh

# One realistic waiting-table row (human prose + machine token), synthetic.
ROW = (
    "| Acme-Handelsstreit (200 €) | von *@acme.example, Betreff Reklamation; "
    "**NICHT** die monatlichen Abo-Belege (noreply@acme.example) "
    "[[match ids=aaaa1111bbbb2222,cccc3333dddd4444 senders=*@acme.example "
    "exclude=noreply@acme.example]] | 01.06.2026 | alle Mails in den Papierkorb |"
)
SECOND_ROW = (
    "| Leasing-Angebotsvergleich | Händler-Angebote "
    "[[match senders=*@dealer-one.example,*@dealer-two.example]] "
    "| 18.06.2026 | gewähltes Angebot ablegen |"
)


def _cases(*rows):
    return rh.parse_waiting_cases("\n".join(rows))


def _mail(gmail_id="", frm="", subject=""):
    return {"gmail_id": gmail_id, "from": frm, "subject": subject}


# ---- parsing -------------------------------------------------------------- #

def test_parse_extracts_ids_senders_exclude_and_name():
    (case,) = _cases(ROW)
    assert case.vorgang == "Acme-Handelsstreit (200 €)"
    assert case.ids == frozenset({"aaaa1111bbbb2222", "cccc3333dddd4444"})
    assert case.senders == ("*@acme.example",)
    assert case.excludes == ("noreply@acme.example",)


def test_parse_ignores_lines_without_token():
    text = "| Vorgang | prose only, no token | 01.06 | act |\n" + SECOND_ROW
    cases = rh.parse_waiting_cases(text)
    assert len(cases) == 1
    assert cases[0].vorgang == "Leasing-Angebotsvergleich"


def test_parse_senders_only_case_has_no_ids():
    (case,) = _cases(SECOND_ROW)
    assert case.ids == frozenset()
    assert case.senders == ("*@dealer-one.example", "*@dealer-two.example")
    assert case.excludes == ()


def test_prose_line_with_token_is_not_a_case():
    # A sentence (or the docs) that merely CONTAINS the token must not become a live
    # case — otherwise its senders glob would silently park real mail (the exact
    # "wrongly parked for weeks" failure this feature exists to prevent).
    prose = "We stopped using [[match senders=*@acme.example]] last year."
    assert rh.parse_waiting_cases(prose) == []


def test_commented_out_row_is_not_a_case():
    # A retired case annotated inside an HTML comment must be inert.
    commented = "<!-- | Old case | retired [[match senders=*@acme.example]] | 01.01 | x | -->"
    assert rh.parse_waiting_cases(commented) == []


# ---- matching ------------------------------------------------------------- #

def test_known_id_matches_and_skips():
    out = rh.match_waiting([_mail(gmail_id="aaaa1111bbbb2222")], _cases(ROW))
    assert out[0]["verdict"] == "skip"
    assert out[0]["vorgang"] == "Acme-Handelsstreit (200 €)"


def test_sender_glob_matches_and_skips():
    out = rh.match_waiting([_mail(frm="support@acme.example")], _cases(ROW))
    assert out[0]["verdict"] == "skip"


def test_exclude_wins_even_when_sender_would_match():
    # noreply@acme.example matches senders=*@acme.example, but is excluded → process.
    out = rh.match_waiting([_mail(frm="noreply@acme.example")], _cases(ROW))
    assert out[0]["verdict"] == "process"
    assert out[0]["vorgang"] is None


def test_no_match_processes():
    out = rh.match_waiting([_mail(gmail_id="ffff", frm="hello@stranger.example")], _cases(ROW))
    assert out[0]["verdict"] == "process"


def test_exclude_wins_over_id_match():
    # The dangerous combo: a mail whose id IS a known case-id AND whose sender is the
    # excluded monthly-receipt sender. exclude must win → process (never parked). Pins
    # the ordering (exclude checked before id) so a reorder can't silently regress.
    row = ("| Case | prose [[match ids=abc123 senders=*@acme.example "
           "exclude=noreply@acme.example]] | 01.06 | act |")
    out = rh.match_waiting(
        [_mail(gmail_id="abc123", frm="noreply@acme.example")], _cases(row))
    assert out[0]["verdict"] == "process"


def test_sender_is_case_insensitive_and_extracts_angle_addr():
    out = rh.match_waiting([_mail(frm="Acme Support <SUPPORT@Acme.Example>")], _cases(ROW))
    assert out[0]["verdict"] == "skip"


def test_glob_does_not_over_match_beyond_domain():
    # *@acme.example must NOT catch a lookalike subdomain suffix.
    out = rh.match_waiting([_mail(frm="x@acme.example.evil.test")], _cases(ROW))
    assert out[0]["verdict"] == "process"


def test_multiple_cases_report_the_right_vorgang():
    cases = _cases(ROW, SECOND_ROW)
    out = rh.match_waiting([_mail(frm="m@dealer-one.example")], cases)
    assert out[0]["verdict"] == "skip"
    assert out[0]["vorgang"] == "Leasing-Angebotsvergleich"


# ---- CLI wiring ----------------------------------------------------------- #

def _write_cfg(tmp_path, *rows):
    p = tmp_path / "config.local.md"
    p.write_text("## Warten-Liste\n\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return str(p)


def test_cli_prints_verdicts_and_exits_zero(tmp_path, capsys):
    cfg = _write_cfg(tmp_path, ROW)
    mails = '[{"gmail_id":"aaaa1111bbbb2222","from":"x@acme.example"},' \
            '{"gmail_id":"zzzz","from":"a@stranger.example"}]'
    rc = rh.main(["match-waiting", "--json", mails, "--config", cfg])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Acme-Handelsstreit" in out
    assert "aaaa1111bbbb2222" in out          # the skipped id is reported
    assert "zzzz" in out                        # the processed id is reported


def test_cli_malformed_json_is_usage_exit_2(tmp_path, capsys):
    cfg = _write_cfg(tmp_path, ROW)
    rc = rh.main(["match-waiting", "--json", "{not json", "--config", cfg])
    assert rc == 2


def test_cli_non_list_json_is_usage_exit_2(tmp_path):
    cfg = _write_cfg(tmp_path, ROW)
    rc = rh.main(["match-waiting", "--json", '{"gmail_id":"a"}', "--config", cfg])
    assert rc == 2


def test_cli_element_not_object_is_usage_exit_2(tmp_path):
    cfg = _write_cfg(tmp_path, ROW)
    rc = rh.main(["match-waiting", "--json", '["not-an-object"]', "--config", cfg])
    assert rc == 2


def test_cli_non_string_field_is_usage_exit_2(tmp_path):
    # A non-string from/gmail_id (LLM quoting slip) must be a clean usage error
    # (exit 2, nothing decided) — never a traceback (exit 1).
    cfg = _write_cfg(tmp_path, ROW)
    rc = rh.main(["match-waiting", "--json", '[{"from":123,"gmail_id":"z"}]', "--config", cfg])
    assert rc == 2
