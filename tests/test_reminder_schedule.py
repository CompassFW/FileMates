"""Unattended-run policy (user-configurable schedule, 2026-06-04).

A scheduled FileMates run is unattended, but the safety model is "ask before destructive".
`decide_unattended(action, mode)` is the single source of truth for what a scheduled run may
EXECUTE itself vs. must QUEUE for the user to confirm next time they're present.

Modes:
  attended     — a human is present; everything executes (the interactive case).
  auto-sort    — create + reversible SORT + rule-covered TRASH execute; ad-hoc DELETE queues.
  collect      — create executes (additive); any mail mutation queues.
  report-only  — nothing executes; everything is just reported/queued.

Actions: create (additive) · sort (reversible label+archive) · trash-rule (reversible Trash,
pre-authorized by a standing user delete-rule) · delete (ad-hoc/unauthorized/permanent; gated).

Safety invariant under mutation test: ad-hoc DELETE never executes unattended; report-only never
executes anything; rule-covered trash-rule executes only in auto-sort/attended.
"""
import reminder_helper as rh


# delete is NEVER executed unattended — the load-bearing guard
def test_delete_queues_in_auto_sort():
    assert rh.decide_unattended("delete", "auto-sort") == "queue"


def test_delete_queues_in_collect():
    assert rh.decide_unattended("delete", "collect") == "queue"


def test_delete_queues_in_report_only():
    assert rh.decide_unattended("delete", "report-only") == "queue"


# auto-sort: create + sort execute (reversible), delete queues
def test_auto_sort_executes_sort():
    assert rh.decide_unattended("sort", "auto-sort") == "execute"


def test_auto_sort_executes_create():
    assert rh.decide_unattended("create", "auto-sort") == "execute"


# trash-rule: a standing user delete-rule pre-authorizes reversible Trash → auto in auto-sort
def test_trash_rule_executes_in_auto_sort():
    assert rh.decide_unattended("trash-rule", "auto-sort") == "execute"


def test_trash_rule_executes_in_attended():
    assert rh.decide_unattended("trash-rule", "attended") == "execute"


def test_trash_rule_queues_in_collect():
    assert rh.decide_unattended("trash-rule", "collect") == "queue"


def test_trash_rule_queues_in_report_only():
    assert rh.decide_unattended("trash-rule", "report-only") == "queue"


# ad-hoc DELETE (not rule-covered) still queues unattended — the rule/ad-hoc distinction
def test_adhoc_delete_still_queues_in_auto_sort_even_with_rules_enabled():
    # rule-covered trash auto-runs, but a deletion WITHOUT a standing rule must still ask
    assert rh.decide_unattended("delete", "auto-sort") == "queue"


# collect: create executes (additive), sort queues
def test_collect_executes_create():
    assert rh.decide_unattended("create", "collect") == "execute"


def test_collect_queues_sort():
    assert rh.decide_unattended("sort", "collect") == "queue"


# report-only: nothing executes
def test_report_only_queues_everything():
    assert rh.decide_unattended("create", "report-only") == "queue"
    assert rh.decide_unattended("sort", "report-only") == "queue"
    assert rh.decide_unattended("delete", "report-only") == "queue"


# attended (human present): everything executes
def test_attended_executes_everything():
    assert rh.decide_unattended("create", "attended") == "execute"
    assert rh.decide_unattended("sort", "attended") == "execute"
    assert rh.decide_unattended("delete", "attended") == "execute"


# an unknown mode must fail safe → queue (never execute on a typo'd config)
def test_unknown_mode_fails_safe_to_queue():
    assert rh.decide_unattended("sort", "totally-unknown") == "queue"
    assert rh.decide_unattended("delete", "") == "queue"
