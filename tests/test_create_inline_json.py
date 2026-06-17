# --------------------------------------------------------------------------- #
# `create --json '<inline>'` — candidates passed as a single argv argument.
#
# Why this exists: unattended runs may not be allowed to WRITE files at all
# (host permission gates on the Write tool / temp dirs). The tool invocation
# itself is typically pre-allowed, so carrying the candidates INSIDE the argv
# of that one plain command removes the file-write dependency entirely.
#
# These tests drive the REAL argv -> main() -> cmd_create wiring (the
# OsascriptRunner construction is monkeypatched to the recording seam), so a
# mutant that parses --json but never feeds it into cmd_create goes red here.
# --------------------------------------------------------------------------- #
import json

import pytest

import reminder_helper as rh


class _Runner:
    """Recording stand-in for OsascriptRunner (same seam as RecordingRunner)."""

    def __init__(self, reminders=None):
        self.calls = []
        self._reminders = reminders or []

    def ensure_list(self, list_name):
        self.calls.append(("ensure_list", list_name))

    def list_reminders(self, list_name):
        self.calls.append(("list_reminders", list_name))
        return self._reminders

    def create_reminder(self, list_name, name, body, due_date=None):
        self.calls.append(("create_reminder", list_name, name, body, due_date))


CANDS = [{
    # Umlauts survive the JSON-unescape -> title path here (in-process argv; the real
    # OS argv byte boundary was additionally smoke-tested live via a subprocess drill).
    "who": "Müller & Söhne",
    "what": "Vertrag prüfen",
    "why": "Frist läuft",
    "topic": "müller vertrag",
    "recap": "Müller & Söhne bitten um Prüfung des Vertrags.",
    "mail_date": "2026-06-11",
    "gmail_id": "zz999",
    "grey_area": False,
}]


def _run_main(monkeypatch, argv, runner=None):
    runner = runner or _Runner()
    monkeypatch.setattr(rh, "OsascriptRunner", lambda: runner)
    rc = rh.main(argv)
    return rc, runner


def test_create_json_inline_creates_via_full_argv_wiring(monkeypatch):
    rc, runner = _run_main(
        monkeypatch, ["--list", "T", "create", "--json", json.dumps(CANDS)])
    assert rc == 0
    created = [c for c in runner.calls if c[0] == "create_reminder"]
    assert len(created) == 1
    _, list_name, name, body, due = created[0]
    assert list_name == "T"
    assert "Müller & Söhne" in name and "Vertrag prüfen" in name
    assert "[gmail:zz999]" in body
    assert due is None                       # AC-R2: no invented deadline


def test_create_all_three_sources_are_equivalent(monkeypatch, tmp_path):
    # --in file, --json inline AND the stdin default must produce byte-equal
    # runner traffic (stdin is the documented interactive workflow and would
    # otherwise be the only uncovered source).
    payload = json.dumps(CANDS, ensure_ascii=False)
    f = tmp_path / "cands.json"
    f.write_text(payload, encoding="utf-8")
    _, via_file = _run_main(monkeypatch, ["--list", "T", "create", "--in", str(f)])
    _, via_json = _run_main(monkeypatch, ["--list", "T", "create", "--json", payload])
    import io
    monkeypatch.setattr(rh.sys, "stdin", io.StringIO(payload))
    _, via_stdin = _run_main(monkeypatch, ["--list", "T", "create"])
    assert via_file.calls == via_json.calls == via_stdin.calls


def test_create_json_and_in_are_mutually_exclusive_exit_2(monkeypatch, tmp_path):
    f = tmp_path / "cands.json"
    f.write_text("[]", encoding="utf-8")

    def _boom():                              # the runner must never be built
        raise AssertionError("OsascriptRunner constructed despite usage error")
    monkeypatch.setattr(rh, "OsascriptRunner", _boom)
    with pytest.raises(SystemExit) as e:
        rh.main(["create", "--in", str(f), "--json", "[]"])
    assert e.value.code == 2                  # argparse usage error, pre-action


def test_create_json_invalid_json_is_clean_usage_error_exit_2(monkeypatch, capsys):
    # A malformed inline payload (the expected LLM quoting slip) must be a clean
    # pre-action USAGE error: exit code 2, a message instead of a traceback, and
    # provably nothing touched (the runner is never even consulted).
    runner = _Runner()
    monkeypatch.setattr(rh, "OsascriptRunner", lambda: runner)
    rc = rh.main(["--list", "T", "create", "--json", "[{not json"])
    assert rc == 2
    assert "invalid --json payload" in capsys.readouterr().err
    assert runner.calls == []                 # pre-action: no list read, no create


def test_create_json_non_list_payload_is_usage_error_exit_2(monkeypatch, capsys):
    # Valid JSON that is not a LIST (e.g. a bare object) is the same usage-error
    # class: exit 2, message, runner untouched — no AttributeError traceback.
    runner = _Runner()
    monkeypatch.setattr(rh, "OsascriptRunner", lambda: runner)
    rc = rh.main(["--list", "T", "create", "--json", "{}"])
    assert rc == 2
    assert "expected a JSON LIST" in capsys.readouterr().err
    assert runner.calls == []


def test_create_subparser_rejects_abbreviated_flags(monkeypatch):
    # allow_abbrev=False is NOT inherited by subparsers — this pins that the create
    # subparser repeats it. `--js` must be rejected as unknown (argparse exit 2),
    # not silently expanded to --json (this repo already had one abbreviation
    # bypass: --force for --force-expunge).
    def _boom():
        raise AssertionError("OsascriptRunner constructed despite usage error")
    monkeypatch.setattr(rh, "OsascriptRunner", _boom)
    with pytest.raises(SystemExit) as e:
        rh.main(["--list", "T", "create", "--js", "[]"])
    assert e.value.code == 2


def test_create_json_explicit_deadline_reaches_due_date(monkeypatch):
    # End-to-end through the --json argv path: an explicitly NAMED deadline must flow
    # JSON -> _candidate_from_json -> decide_due_date -> create_reminder(due_date=...).
    # (Previously only the no-deadline case (due is None, AC-R2) was wired-tested.)
    cands = [dict(CANDS[0], explicit_deadline="2026-07-01")]
    rc, runner = _run_main(monkeypatch, ["--list", "T", "create", "--json", json.dumps(cands)])
    assert rc == 0
    created = [c for c in runner.calls if c[0] == "create_reminder"]
    assert len(created) == 1
    assert created[0][4] == "2026-07-01"   # the due_date argument


def test_create_json_malformed_element_is_usage_error_exit_2(monkeypatch, capsys):
    # A well-formed LIST whose ELEMENT is malformed (here: a non-object) is the same
    # LLM-quoting-slip class -> clean usage error (exit 2, nothing created), not a
    # traceback. Covers the element-shape guard, not just the list-shape guard.
    runner = _Runner()
    monkeypatch.setattr(rh, "OsascriptRunner", lambda: runner)
    rc = rh.main(["--list", "T", "create", "--json", "[1, 2, 3]"])
    assert rc == 2
    assert "invalid candidate" in capsys.readouterr().err
    assert all(c[0] != "create_reminder" for c in runner.calls)


def test_create_json_element_missing_gmail_id_is_exit_2(monkeypatch, capsys):
    runner = _Runner()
    monkeypatch.setattr(rh, "OsascriptRunner", lambda: runner)
    rc = rh.main(["--list", "T", "create", "--json",
                  json.dumps([{"who": "X", "topic": "t", "recap": "r", "mail_date": "2026-06-11"}])])
    assert rc == 2
    assert all(c[0] != "create_reminder" for c in runner.calls)


def test_create_json_dry_run_plans_but_creates_nothing(monkeypatch):
    rc, runner = _run_main(
        monkeypatch, ["--list", "T", "create", "--json", json.dumps(CANDS), "--dry-run"])
    assert rc == 0
    assert all(c[0] != "create_reminder" for c in runner.calls)
