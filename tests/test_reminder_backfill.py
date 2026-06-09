"""decide_create precedence — surfaced by the live smoke (2026-06-04).

Real data showed the SAME gmail-id duplicated up to 3× across open+done (legacy agent-prose
that did not dedup at all). After identifying tasks, a task-key can end up in BOTH the open and
completed sets. Such a task is ACTIVELY open (no check-off = not done, AC-R8) → 'skip-open-dup',
NOT 'ask-repeat-of-done'. The ask-branch is reserved for done-AND-not-currently-open (a recurring
obligation arriving again after the previous one was checked off).

(The in-place backfill transform `insert_task_anchor` was removed: the chosen migration is
wipe-and-repopulate, so an in-place anchor utility was unused — YAGNI. Rebuild properly if ever
needed.)

Mutation target: `decide_create` open-wins precedence.
"""
import reminder_helper as rh


def test_decide_create_open_wins_when_key_in_both_states():
    # A task duplicated into both open and done (legacy artifact) is ACTIVELY open → skip, don't nag.
    key = rh.task_key("Acme Belege")
    assert rh.decide_create(key, {key}, {key}) == "skip-open-dup"


def test_decide_create_completed_only_still_asks():
    # Genuinely done last time, not currently open → ask before re-creating (recurring obligation).
    key = rh.task_key("Beispielsteuer 2023")
    assert rh.decide_create(key, set(), {key}) == "ask-repeat-of-done"


def test_decide_create_open_only_skips():
    key = rh.task_key("Belege hochladen")
    assert rh.decide_create(key, {key}, set()) == "skip-open-dup"


def test_decide_create_unseen_creates():
    assert rh.decide_create(rh.task_key("BeispielVendor einreichen"), set(), set()) == "create"
