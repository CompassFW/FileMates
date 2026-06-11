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
    "who": "Müller & Söhne",                      # umlauts must survive argv -> title
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


def test_create_json_inline_equivalent_to_in_file(monkeypatch, tmp_path):
    f = tmp_path / "cands.json"
    f.write_text(json.dumps(CANDS), encoding="utf-8")
    _, via_file = _run_main(monkeypatch, ["--list", "T", "create", "--in", str(f)])
    _, via_json = _run_main(
        monkeypatch, ["--list", "T", "create", "--json", json.dumps(CANDS)])
    assert via_file.calls == via_json.calls   # byte-equal plans + creations


def test_create_json_and_in_are_mutually_exclusive_exit_2(monkeypatch, tmp_path):
    f = tmp_path / "cands.json"
    f.write_text("[]", encoding="utf-8")

    def _boom():                              # the runner must never be built
        raise AssertionError("OsascriptRunner constructed despite usage error")
    monkeypatch.setattr(rh, "OsascriptRunner", _boom)
    with pytest.raises(SystemExit) as e:
        rh.main(["create", "--in", str(f), "--json", "[]"])
    assert e.value.code == 2                  # argparse usage error, pre-action


def test_create_json_invalid_json_fails_loud_creates_nothing(monkeypatch):
    runner = _Runner()
    monkeypatch.setattr(rh, "OsascriptRunner", lambda: runner)
    with pytest.raises(json.JSONDecodeError):
        rh.main(["--list", "T", "create", "--json", "[{not json"])
    assert all(c[0] != "create_reminder" for c in runner.calls)


def test_create_json_dry_run_plans_but_creates_nothing(monkeypatch):
    rc, runner = _run_main(
        monkeypatch, ["--list", "T", "create", "--json", json.dumps(CANDS), "--dry-run"])
    assert rc == 0
    assert all(c[0] != "create_reminder" for c in runner.calls)
