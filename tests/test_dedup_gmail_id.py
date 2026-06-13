# --------------------------------------------------------------------------- #
# Second dedup axis: the STABLE gmail-id, not just the LLM-supplied task topic.
#
# Real production bug (Zoe, 2026-06-11): the same actionable mail produced TWO
# open reminders across two runs because the LLM phrased `topic` differently
# each time ("enbw abbuchung fehlgeschlagen 06-2026" vs "...fehlgeschlagen") —
# different task_key → task-level dedup missed → duplicate. The gmail id was
# identical and stable the whole time.
#
# Fix (user choice = SKIP): if a candidate's gmail_id already has an OPEN
# reminder in the LIVE (pre-run) list and no task-key match fired, silently skip
# it (never a duplicate). The within-run multi-task case (AC-R7) MUST stay
# intact: several tasks for one mail passed in ONE run all create, because the
# guard checks only the frozen pre-run snapshot, never within-run siblings.
# --------------------------------------------------------------------------- #
from datetime import date

import reminder_helper as rh


def _cand(topic, gmail_id, what="X"):
    return rh.TaskCandidate(
        who="EnBW", what=what, why=None, recap="r", mail_date=date(2026, 6, 11),
        gmail_id=gmail_id, topic=topic)


def _open_rem(topic, gmail_id, completed=False):
    body = rh.build_notes("r", date(2026, 6, 11), gmail_id, task_key=rh.task_key(topic))
    return rh.Reminder(name="t", body=body, completed=completed)


# --- pure layer ------------------------------------------------------------ #
def test_same_mail_drifted_topic_is_skipped_not_created():
    # The exact Zoe bug: mail X already open under one topic; a new run offers the
    # SAME mail under a drifted topic → no task-key match → gmail guard → SKIP.
    open_keys = {rh.task_key("enbw abbuchung fehlgeschlagen 06-2026")}
    plan = rh.plan_creations(
        [_cand("enbw abbuchung fehlgeschlagen", "19eb7bf5b438d95c")],
        open_keys, set(), open_gmail_ids={"19eb7bf5b438d95c"})
    assert plan["create"] == []          # no duplicate
    assert plan["asks"] == []            # skip is silent (user's choice), not an ask


def test_ac_r7_multiple_tasks_one_mail_one_run_all_create():
    # AC-R7: two genuine tasks from the SAME mail, passed in ONE run, with the mail
    # NOT yet in the live list → BOTH must be created. The guard must not fire on
    # within-run siblings.
    plan = rh.plan_creations(
        [_cand("task one", "mailM", what="A"), _cand("task two", "mailM", what="B")],
        set(), set(), open_gmail_ids=frozenset())
    assert len(plan["create"]) == 2
    assert plan["asks"] == []


def test_completed_mail_id_does_not_block_a_new_open_task():
    # Only OPEN gmail-ids block. A fully-completed prior mail must not silently
    # swallow a genuinely new task (that path stays governed by task-key + R3).
    plan = rh.plan_creations(
        [_cand("fresh task", "mailDone")],
        set(), set(), open_gmail_ids=frozenset())   # completed ids are simply not in the open set
    assert len(plan["create"]) == 1


def test_default_open_gmail_ids_preserves_legacy_behavior():
    # Omitting the new arg must behave exactly as before (no accidental blocking).
    plan = rh.plan_creations([_cand("any", "mailZ")], set(), set())
    assert len(plan["create"]) == 1


def test_open_gmail_ids_from_reminders_counts_only_open_filemates():
    rems = [
        _open_rem("a", "open1", completed=False),
        _open_rem("b", "done1", completed=True),
        rh.Reminder(name="manual", body="no anchors here", completed=False),
    ]
    ids = rh.open_gmail_ids_from_reminders(rems)
    assert ids == {"open1"}


# --- wiring: cmd_create reads the live list and feeds the guard ------------ #
class _Runner:
    def __init__(self, reminders):
        self.calls = []
        self._reminders = reminders

    def ensure_list(self, n):
        self.calls.append(("ensure_list", n))

    def list_reminders(self, n):
        self.calls.append(("list_reminders", n))
        return self._reminders

    def create_reminder(self, n, name, body, due_date=None):
        self.calls.append(("create_reminder", name))


def test_cmd_create_wiring_skips_same_mail_across_runs(monkeypatch):
    # End-to-end through argv → main → cmd_create: the live list already holds an
    # OPEN reminder for mail X (drifted topic); a new --json candidate for the same
    # mail must NOT reach create_reminder.
    live = [_open_rem("enbw abbuchung fehlgeschlagen 06-2026", "19eb7bf5b438d95c")]
    runner = _Runner(live)
    monkeypatch.setattr(rh, "OsascriptRunner", lambda: runner)
    import json
    payload = json.dumps([{
        "who": "EnBW", "what": "bezahlen", "topic": "enbw abbuchung fehlgeschlagen",
        "recap": "r", "mail_date": "2026-06-11", "gmail_id": "19eb7bf5b438d95c",
        "grey_area": False}])
    rc = rh.main(["--list", "Email-Tasks", "create", "--json", payload])
    assert rc == 0
    assert all(c[0] != "create_reminder" for c in runner.calls)
