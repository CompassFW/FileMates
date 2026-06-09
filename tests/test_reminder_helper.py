"""RED acceptance suite for the FileMates reminder-helper (Phase 1 TDD).

Every test here FAILS now (the skeleton raises NotImplementedError) and must turn
GREEN only against a correct implementation. The fakes model ONLY the two seams
(MailResolver / OsascriptRunner) and record the exact calls issued — mirroring
RecordingIMAP in test_delete_safety.py. The decision functions are exercised
directly, never mocked.

MUTATION-INTENT (the two load-bearing invariants these tests exist to catch if a
later mutation breaks them):

  * AC-R1 (exactly-one / never-bulk): react_to_checkoffs must issue resolver.sort
    EXACTLY ONCE, with the single explicit gmail id and mapped label, and NEVER on
    the 0/≥2/no-label/sibling-open/manual paths. A mutation that bulk-archives, uses
    a predicate, or sorts an unmapped/ambiguous mail must fail here. (This is the
    regression guard for the historical "archived ALL mails" bug.)
  * AC-R3 (no duplicate / repeat-of-done → ask): decide_create / plan_creations must
    never silently (re-)create for an open-dup or a previously-completed task. A
    mutation that drops the dedup or the repeat-of-done→ask must fail here.

Evidence class of THIS file (per the spec's Reality Ledger): the decision core is
unit-tested; the orchestration is `seam-contract` (proves the EMITTED command, not
the real Gmail/Reminders effect). The end-to-end reality — a real hex-id→one-mail
resolve, a real check-off round-trip, and `[gmail:<id>]` surviving iCloud-sync —
stays RED (`integration-fake`) until the human smoke and is NOT claimed by this file.
"""
from datetime import date

import reminder_helper as rh


# --------------------------------------------------------------------------- #
# Fakes that model ONLY the seams (record calls; return caller-chosen values),
# exactly like RecordingIMAP. They never replace a decision function.
# --------------------------------------------------------------------------- #
class RecordingResolver:
    """Models the MailResolver seam: records every resolve()/sort() and returns a
    caller-chosen resolve-count, so a test asserts WHICH mail commands were issued
    (the real selection mechanism), not merely that a function returned."""

    def __init__(self, resolve_count=1, sort_ok=True):
        self.calls = []
        self._resolve_count = resolve_count
        self._sort_ok = sort_ok

    def resolve(self, gmail_id):
        self.calls.append(("resolve", gmail_id))
        return self._resolve_count

    def sort(self, gmail_id, label):
        self.calls.append(("sort", gmail_id, label))
        return self._sort_ok


class RecordingRunner:
    """Models the OsascriptRunner seam: records ensure_list()/list_reminders()/
    create_reminder() and returns caller-supplied reminders from list_reminders()."""

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


# helpers for building seam fixtures
def _rem(name, body, completed, due=None):
    return rh.Reminder(name=name, body=body, completed=completed, due_date=due)


# =========================================================================== #
# PURE-DECISION LAYER
# =========================================================================== #

# --- parse_token (AC-R1 anchor / S2-minimal: ambiguous => ask) ------------- #
def test_parse_token_single_wellformed_returns_id():
    # AC-R5/S2-minimal: exactly one well-formed token -> the id.
    assert rh.parse_token("recap\nMail vom 03.06.2026\n[gmail:aaa111]") == "aaa111"


def test_parse_token_missing_returns_none():
    # S2-minimal: no token -> None (caller must ask, never act).
    assert rh.parse_token("just some notes, no anchor") is None


def test_parse_token_malformed_returns_none():
    # S2-minimal: user-edited / malformed token -> None.
    assert rh.parse_token("[gmail:]") is None
    assert rh.parse_token("[gmail:zzz nope]") is None


def test_parse_token_two_tokens_is_ambiguous_returns_none():
    # S2-minimal: TWO tokens is ambiguous -> None (ask, never fuzzy-match).
    assert rh.parse_token("[gmail:abc123] ... [gmail:def456]") is None


# --- is_filemates_reminder (AC-R8: manual reminders off-limits) ------------ #
def test_is_filemates_reminder_true_with_token():
    # token present -> a FileMates reminder, eligible to act on.
    assert rh.is_filemates_reminder("body\n[gmail:abc123]") is True


def test_is_filemates_reminder_false_for_manual():
    # AC-R8: a manual reminder (no token) is OFF-LIMITS -> False.
    assert rh.is_filemates_reminder("Notar Auslage zahlen") is False


# --- decide_mail_action: the FULL truth table (AC-R1 + S2-minimal) --------- #
def test_decide_mail_action_one_and_mapped_sorts():
    # AC-R1: exactly one mail AND a label mapped -> 'sort' (the ONLY action path).
    assert rh.decide_mail_action(1, True) == "sort"


def test_decide_mail_action_zero_resolved_asks():
    # AC-R1: id resolves to no mail -> 'ask' (never act on a different mail).
    assert rh.decide_mail_action(0, True) == "ask"


def test_decide_mail_action_two_resolved_asks():
    # AC-R1: id resolves to >1 mail -> 'ask' (the bulk-archive guard).
    assert rh.decide_mail_action(2, True) == "ask"


def test_decide_mail_action_one_but_unmapped_asks():
    # AC-R1: exactly one mail but NO label mapped -> 'ask' (never guess the category).
    assert rh.decide_mail_action(1, False) == "ask"


# --- decide_due_date (AC-R2: never invent a deadline) ---------------------- #
def test_decide_due_date_with_explicit_deadline():
    # AC-R2: an explicitly named deadline -> the exact ISO `YYYY-MM-DD` string.
    assert rh.decide_due_date(date(2026, 6, 30)) == "2026-06-30"


def test_decide_due_date_none_when_no_deadline():
    # AC-R2: no deadline named -> None (never a phantom date).
    assert rh.decide_due_date(None) is None


# --- build_title (AC-R4: Wer-Was-Warum, drop missing, never id) ------------ #
def test_build_title_full_who_what_why():
    # AC-R4: full triple -> `Wer - Was - Warum`.
    t = rh.build_title("Alex Beispiel", "Umzug Firma", "Wegen Rückerstattung Umzugskosten")
    assert t == "Alex Beispiel - Umzug Firma - Wegen Rückerstattung Umzugskosten"


def test_build_title_missing_why_is_dropped_not_invented():
    # AC-R4/R2: a None part is DROPPED, not fabricated.
    t = rh.build_title("Alex Beispiel", "Umzug Firma", None)
    assert t == "Alex Beispiel - Umzug Firma"
    assert " - None" not in t and "Warum" not in t


def test_build_title_missing_who_is_dropped():
    # AC-R4: missing 'who' likewise dropped, no leading separator.
    t = rh.build_title(None, "Umzug Firma", "Wegen Rückerstattung")
    assert t == "Umzug Firma - Wegen Rückerstattung"
    assert not t.startswith("-")


def test_build_title_grey_area_gets_review_prefix():
    # AC-R4: grey-area keeps the `Review: ` prefix.
    t = rh.build_title("Finanzamt", "Steuerbescheid prüfen", None, grey_area=True)
    assert t.startswith("Review: ")
    assert "Finanzamt - Steuerbescheid prüfen" in t


def test_build_title_never_contains_gmail_id():
    # AC-R4: the `[gmail:<id>]` anchor lives in the notes, NEVER in the title.
    t = rh.build_title("Beispielbank", "Belege einreichen", "BeispielVendor")
    assert "gmail:" not in t and "[" not in t


# --- format_mail_date (AC-R5: zero-padded German date) --------------------- #
def test_format_mail_date_zero_padded_german():
    # AC-R5: `Mail vom TT.MM.JJJJ`, zero-padded.
    assert rh.format_mail_date(date(2026, 6, 3)) == "Mail vom 03.06.2026"


# --- build_notes (AC-R5: order; gefilt iff filed; id is LAST line) --------- #
def test_build_notes_order_without_attachment():
    # AC-R5: recap first, then `Mail vom ...`, then the id as the LAST line; no gefilt.
    notes = rh.build_notes("Kurzer Recap.", date(2026, 6, 3), "abc123")
    lines = notes.splitlines()
    assert lines[0] == "Kurzer Recap."
    assert lines[1] == "Mail vom 03.06.2026"
    assert lines[-1] == "[gmail:abc123]"
    assert "gefilt:" not in notes  # absent when nothing filed


def test_build_notes_with_attachment_includes_gefilt_before_id():
    # AC-R5: `gefilt: <path>` present IFF an attachment was filed, and the id stays LAST.
    notes = rh.build_notes("Recap.", date(2026, 6, 3), "abc123",
                           filed_path="/Belege/2026/06/x.pdf")
    lines = notes.splitlines()
    assert "gefilt: /Belege/2026/06/x.pdf" in lines
    assert lines[-1] == "[gmail:abc123]"  # id is still the mandatory last line
    assert lines.index("gefilt: /Belege/2026/06/x.pdf") < len(lines) - 1


# --- task_key (AC-R3: task identity, not surface form) --------------------- #
def test_task_key_same_topic_different_surface_collapses():
    # AC-R3: case/whitespace variants of the same task -> ONE key.
    assert rh.task_key("Acme Belege") == rh.task_key("  acme   belege ")


def test_task_key_same_task_across_two_mails_collapses():
    # AC-R3: the same real task arriving in different mails -> one key (the live bug).
    assert rh.task_key("Beispielsteuer 2023") == rh.task_key("BEISPIELSTEUER 2023")


def test_task_key_different_topics_differ():
    # AC-R3: genuinely different tasks -> different keys.
    assert rh.task_key("Acme Belege") != rh.task_key("Beispielsteuer 2023")


# --- decide_create (AC-R3 revised: open-dup / repeat-of-done) -------------- #
def test_decide_create_unseen_key_creates():
    # AC-R3: a task neither open nor completed -> 'create'.
    assert rh.decide_create("BeispielVendor Statements", set(), set()) == "create"


def test_decide_create_open_key_skips():
    # AC-R3: same task already OPEN -> 'skip-open-dup' (incl. overdue).
    assert rh.decide_create("k1", {"k1"}, set()) == "skip-open-dup"


def test_decide_create_completed_acme_asks_repeat_of_done():
    # AC-R3 (a real-world false-positive): "Acme Belege" previously checked off
    # -> 'ask-repeat-of-done', NEVER a silent re-create.
    key = rh.task_key("Acme Belege")
    assert rh.decide_create(key, set(), {key}) == "ask-repeat-of-done"


def test_decide_create_completed_beispielsteuer_asks_repeat_of_done():
    # AC-R3 (a real-world false-positive): "Beispielsteuer 2023 1.234,56 €" done/paid.
    key = rh.task_key("Beispielsteuer 2023 1.234,56 €")
    assert rh.decide_create(key, set(), {key}) == "ask-repeat-of-done"


# --- all_siblings_done (AC-R1 x R7) ---------------------------------------- #
def test_all_siblings_done_false_when_one_open():
    # AC-R1xR7: two reminders on one mail, one still open -> False (don't sort yet).
    rems = [
        _rem("A", "x\n[gmail:mail1]", completed=True),
        _rem("B", "y\n[gmail:mail1]", completed=False),
    ]
    assert rh.all_siblings_done("mail1", rems) is False


def test_all_siblings_done_true_when_all_completed():
    # AC-R1xR7: only once the LAST sibling is checked off -> True.
    rems = [
        _rem("A", "x\n[gmail:mail1]", completed=True),
        _rem("B", "y\n[gmail:mail1]", completed=True),
    ]
    assert rh.all_siblings_done("mail1", rems) is True


# --- decide_idempotent_action (AC-R6 decision part) ------------------------ #
def test_decide_idempotent_action_already_archived_is_noop():
    # AC-R6: acting on an already-archived mail -> safe 'noop'.
    assert rh.decide_idempotent_action(True) == "noop"


def test_decide_idempotent_action_not_archived_sorts():
    # AC-R6: not yet archived -> 'sort'.
    assert rh.decide_idempotent_action(False) == "sort"


# =========================================================================== #
# SEAM-CONTRACT TESTS (S1) — the regression guard for the bulk-archive bug.
# Assert the EXACT recorded call list on the seams (like imap.calls == [...]).
# =========================================================================== #

LIST = "Email-Tasks"


# --- AC-R1 happy path: exactly ONE explicit-id sort, nothing else ---------- #
def test_react_happy_path_sorts_exactly_one_explicit_id():
    # AC-R1: one completed FileMates reminder, resolves to 1, label mapped ->
    # resolver.sort called EXACTLY ONCE with that ONE explicit id + mapped label,
    # and NO other sort. The recorded calls must be exactly resolve+sort for that id.
    runner = RecordingRunner(reminders=[_rem("A", "recap\n[gmail:mail1]", completed=True)])
    resolver = RecordingResolver(resolve_count=1)
    report = rh.react_to_checkoffs(runner, resolver, LIST, {"mail1": "Clients/Acme"})

    assert resolver.calls == [
        ("resolve", "mail1"),
        ("sort", "mail1", "Clients/Acme"),
    ]
    # never a bulk/predicate sort: only the single explicit-id sort above exists.
    assert [c for c in resolver.calls if c[0] == "sort"] == [("sort", "mail1", "Clients/Acme")]
    assert "mail1" in report["sorted"]


# --- AC-R1 ask paths: resolve 0 / 2 -> NEVER sort -------------------------- #
def test_react_resolve_zero_never_sorts_and_asks():
    # AC-R1: id resolves to no mail -> resolver.sort NEVER called; mail in 'asks'.
    runner = RecordingRunner(reminders=[_rem("A", "recap\n[gmail:mail1]", completed=True)])
    resolver = RecordingResolver(resolve_count=0)
    report = rh.react_to_checkoffs(runner, resolver, LIST, {"mail1": "Clients/Acme"})

    assert [c for c in resolver.calls if c[0] == "sort"] == []  # NEVER sorted
    assert "mail1" in report["asks"]


def test_react_resolve_two_never_sorts_and_asks():
    # AC-R1: id resolves to >1 mail -> resolver.sort NEVER called (THE bulk guard).
    runner = RecordingRunner(reminders=[_rem("A", "recap\n[gmail:mail1]", completed=True)])
    resolver = RecordingResolver(resolve_count=2)
    report = rh.react_to_checkoffs(runner, resolver, LIST, {"mail1": "Clients/Acme"})

    assert [c for c in resolver.calls if c[0] == "sort"] == []
    assert "mail1" in report["asks"]


def test_react_unmapped_label_never_sorts_and_asks():
    # AC-R1: exactly one mail but no label mapped -> ask, never sort.
    runner = RecordingRunner(reminders=[_rem("A", "recap\n[gmail:mail1]", completed=True)])
    resolver = RecordingResolver(resolve_count=1)
    report = rh.react_to_checkoffs(runner, resolver, LIST, {})  # empty label map
    assert [c for c in resolver.calls if c[0] == "sort"] == []
    assert "mail1" in report["asks"]


# --- AC-R8 / manual + open reminders are off-limits ------------------------ #
def test_react_manual_reminder_is_never_sorted():
    # AC-R8: a manual reminder (no token) -> resolver.sort NEVER called for it,
    # and resolve is never even attempted (it is not a FileMates reminder).
    runner = RecordingRunner(reminders=[_rem("Notar Auslage", "no token here", completed=True)])
    resolver = RecordingResolver(resolve_count=1)
    rh.react_to_checkoffs(runner, resolver, LIST, {"x": "y"})
    assert resolver.calls == []  # no resolve, no sort — manual reminder untouched


def test_react_open_filemates_reminder_is_never_sorted():
    # AC-R8: an OPEN (not checked-off) FileMates reminder -> never sorted (the
    # user's check-off is the ONLY done signal).
    runner = RecordingRunner(reminders=[_rem("A", "recap\n[gmail:mail1]", completed=False)])
    resolver = RecordingResolver(resolve_count=1)
    rh.react_to_checkoffs(runner, resolver, LIST, {"mail1": "Clients/Acme"})
    assert [c for c in resolver.calls if c[0] == "sort"] == []


# --- AC-R1 x R7: sibling still open -> never sort -------------------------- #
def test_react_sibling_open_never_sorts():
    # AC-R1xR7: a completed reminder whose sibling (same id) is still open ->
    # resolver.sort NEVER called until BOTH are done (mail must not vanish early).
    runner = RecordingRunner(reminders=[
        _rem("A", "recap\n[gmail:mail1]", completed=True),
        _rem("B", "recap2\n[gmail:mail1]", completed=False),
    ])
    resolver = RecordingResolver(resolve_count=1)
    rh.react_to_checkoffs(runner, resolver, LIST, {"mail1": "Clients/Acme"})
    assert [c for c in resolver.calls if c[0] == "sort"] == []


# --- apply_creations: ensure_list then create_reminder per spec ------------ #
def test_apply_creations_ensures_list_then_creates_each_spec():
    # AC-R9: ensure_list first, then exactly one create_reminder per spec, with the
    # spec's title/notes/due — asserted as the exact recorded call list.
    runner = RecordingRunner()
    specs = [
        rh.ReminderSpec(title="Beispielbank - Belege - BeispielVendor",
                        notes="Recap.\nMail vom 03.06.2026\n[gmail:mail1]", due_date=None),
        rh.ReminderSpec(title="Finanzamt - Bescheid prüfen",
                        notes="Recap2.\nMail vom 04.06.2026\n[gmail:mail2]",
                        due_date="2026-06-30"),
    ]
    rh.apply_creations(runner, LIST, specs)
    assert runner.calls == [
        ("ensure_list", LIST),
        ("create_reminder", LIST, "Beispielbank - Belege - BeispielVendor",
         "Recap.\nMail vom 03.06.2026\n[gmail:mail1]", None),
        ("create_reminder", LIST, "Finanzamt - Bescheid prüfen",
         "Recap2.\nMail vom 04.06.2026\n[gmail:mail2]", "2026-06-30"),
    ]


# --- plan_creations: create-candidate -> spec; repeat-of-done -> ask ------- #
def test_plan_creations_create_candidate_builds_spec_obeying_r4_r5():
    # AC-R2..R5,R7: a 'create' candidate yields a ReminderSpec whose title is the
    # Wer-Was-Warum (no id) and whose notes end with the id; no due when no deadline.
    cand = rh.TaskCandidate(
        who="Beispielbank", what="Belege einreichen", why="BeispielVendor",
        recap="Bitte BeispielVendor-Belege einreichen.",
        mail_date=date(2026, 6, 3), gmail_id="mail1",
        topic="BeispielVendor Belege einreichen",
        explicit_deadline=None, filed_path=None, grey_area=False,
    )
    plan = rh.plan_creations([cand], open_keys=set(), completed_keys=set())
    assert len(plan["create"]) == 1
    spec = plan["create"][0]
    assert spec.title == "Beispielbank - Belege einreichen - BeispielVendor"
    assert "gmail:" not in spec.title                      # AC-R4: id never in title
    assert spec.notes.splitlines()[-1] == "[gmail:mail1]"  # AC-R5: id is LAST line
    assert spec.due_date is None                           # AC-R2: no invented deadline
    assert plan["asks"] == []


def test_plan_creations_repeat_of_done_asks_not_creates():
    # AC-R3: a previously-completed task -> recorded as an 'ask', NEVER a create.
    cand = rh.TaskCandidate(
        who="Acme", what="Belege", why=None,
        recap="Acme Belege hochladen.",
        mail_date=date(2026, 6, 3), gmail_id="mail9",
        topic="Acme Belege",
        explicit_deadline=None, filed_path=None, grey_area=False,
    )
    # AC-R3 (revised): dedup keys on task_key(candidate.topic), NOT who/what/why.
    done_key = rh.task_key("Acme Belege")  # the SAME topic, already checked off
    plan = rh.plan_creations([cand], open_keys=set(), completed_keys={done_key})
    assert plan["create"] == []        # never silently re-created
    assert len(plan["asks"]) == 1


# =========================================================================== #
# NEW RED CONTRACTS — dev-review defects (B1 / S1 / S2 / N1).
# These pin the *corrected* contract from the "Build decision — task-key
# persistence" note and the Phase-2 code/security review. They FAIL against the
# current code (the contract is not yet implemented) and turn GREEN only once
# the coder implements the persisted-task-anchor + line-preserving emit + the
# blank-topic and whitespace-title guards. No existing test is weakened.
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# B1 — task-key persisted as a [task:<key>] anchor (the recurring-task bug).
# build_notes must gain a task_key param; the [task:<key>] line sits immediately
# BEFORE the mandatory-last [gmail:<id>] line. A new pure parse_task_anchor reads
# it back. The cross-run dedup round-trip is the test that would have caught B1.
# Evidence: PURE (in-memory build -> Reminder -> parse). The iCloud-sync survival
# of the anchor is the separate integration-fake/RED leg already in the ledger and
# is NOT re-asserted here.
# --------------------------------------------------------------------------- #
def test_build_notes_persists_task_anchor_before_gmail_id_last():
    # B1/AC-R5 (revised order): recap -> Mail vom -> [task:<key>] -> [gmail:<id>] (LAST).
    key = rh.task_key("Acme Belege")
    notes = rh.build_notes("Recap.", date(2026, 6, 3), "abc123", task_key=key)
    lines = notes.splitlines()
    assert lines[0] == "Recap."
    assert lines[1] == "Mail vom 03.06.2026"
    assert f"[task:{key}]" in lines                 # the persisted task anchor
    assert lines[-1] == "[gmail:abc123]"            # id STILL the mandatory last line
    # ordering: the task anchor sits immediately before the id (the id stays last).
    assert lines.index(f"[task:{key}]") == lines.index("[gmail:abc123]") - 1


def test_build_notes_full_order_with_gefilt_and_task_anchor():
    # B1/AC-R5: recap -> Mail vom -> gefilt: (iff filed) -> [task:<key>] -> [gmail:<id>] (LAST).
    key = rh.task_key("Beispielsteuer 2023")
    notes = rh.build_notes("Recap.", date(2026, 6, 3), "abc123",
                           task_key=key, filed_path="/Belege/2026/06/x.pdf")
    lines = notes.splitlines()
    assert lines == [
        "Recap.",
        "Mail vom 03.06.2026",
        "gefilt: /Belege/2026/06/x.pdf",
        f"[task:{key}]",
        "[gmail:abc123]",
    ]


def test_build_notes_without_task_key_unchanged_no_task_anchor():
    # B1: task_key defaults to None -> NO [task:...] line; id stays last (back-compat
    # with the existing AC-R5 notes shape).
    notes = rh.build_notes("Recap.", date(2026, 6, 3), "abc123")
    assert "[task:" not in notes
    assert notes.splitlines()[-1] == "[gmail:abc123]"


def test_parse_task_anchor_single_wellformed_returns_key():
    # B1: exactly one well-formed [task:<key>] -> the key (mirrors parse_token).
    body = "Recap.\nMail vom 03.06.2026\n[task:acme belege]\n[gmail:abc123]"
    assert rh.parse_task_anchor(body) == "acme belege"


def test_parse_task_anchor_missing_returns_none():
    # B1: no task anchor -> None (S2-minimal degrade-safe, mirrors parse_token).
    assert rh.parse_task_anchor("Recap.\n[gmail:abc123]") is None


def test_parse_task_anchor_ambiguous_returns_none():
    # B1: two task anchors is ambiguous -> None (ask, never guess the task identity).
    body = "[task:one]\n[task:two]\n[gmail:abc123]"
    assert rh.parse_task_anchor(body) is None


def test_b1_cross_run_dedup_roundtrip_recognises_repeat_of_done():
    # B1 — THE regression test. A live reminder whose notes were built with
    # task_key(topic) must, when read back, expose a key that EQUALS
    # task_key(candidate.topic) for a new candidate of the same topic — so a
    # completed reminder drives decide_create -> 'ask-repeat-of-done' (never a
    # silent re-create). This is exactly the Acme/Beispielsteuer ~6x live bug:
    # before persistence, the read-back key (derived from the title) never matched
    # the create-time key (derived from topic) -> dedup was blind across runs.
    topic = "Acme Belege"
    notes = rh.build_notes("Acme Belege hochladen.", date(2026, 6, 3),
                           "mail9", task_key=rh.task_key(topic))
    # the reminder as read back from the live list (completed = user checked it off):
    reminder = rh.Reminder(name="Acme - Belege", body=notes, completed=True)

    # 1) the key persisted in the notes round-trips to task_key(topic):
    assert rh.parse_task_anchor(reminder.body) == rh.task_key(topic)

    # 2) it therefore feeds dedup as a COMPLETED key -> repeat-of-done -> ASK:
    cand = rh.TaskCandidate(
        who="Acme", what="Belege", why=None,
        recap="Acme Belege hochladen.",
        mail_date=date(2026, 6, 4), gmail_id="mailNEW",
        topic=topic, explicit_deadline=None, filed_path=None, grey_area=False,
    )
    completed_keys = {rh.parse_task_anchor(reminder.body)}
    assert rh.decide_create(rh.task_key(cand.topic), set(), completed_keys) == "ask-repeat-of-done"
    plan = rh.plan_creations([cand], open_keys=set(), completed_keys=completed_keys)
    assert plan["create"] == []                     # never silently re-created
    assert len(plan["asks"]) == 1


def test_b1_keys_from_reminders_reads_anchor_not_title_kills_title_revert():
    # B1 — THE WIRING regression test (closes the surviving-mutant gap). The other
    # B1 tests assert on parse_task_anchor + decide_create with hand-built keys; they
    # NEVER drive _keys_from_reminders, the composition root where B1 actually lived —
    # so a revert of _keys_from_reminders to task_key(rem.name) (the original bug) still
    # passes them. This test drives the REAL wiring helper end-to-end.
    #
    # Build a LIVE reminder exactly as the system does: notes via build_notes with the
    # create-time task identity persisted as the [task:<key>] anchor, completed=True.
    # CRUCIALLY the reminder's title is the HUMAN title "Acme - Belege hochladen",
    # which does NOT collapse to task_key("Acme Belege") — so a title-based
    # read-back (the mutant) yields the WRONG key and the cross-run dedup match is missed.
    topic = "Acme Belege"
    anchor_key = rh.task_key(topic)                  # what create-time persisted: "acme belege"
    human_title = "Acme - Belege hochladen"          # the on-device title (NOT the topic)
    # sanity: the title deliberately does NOT collapse to the anchor key — this divergence
    # is precisely what a title-based read-back gets wrong (and what makes this kill the mutant).
    assert rh.task_key(human_title) != anchor_key

    notes = rh.build_notes("Acme Belege hochladen.", date(2026, 6, 3),
                           "mail9", task_key=anchor_key)
    reminder = rh.Reminder(name=human_title, body=notes, completed=True)

    # Drive the REAL wiring helper that feeds cmd_create its live key sets:
    open_keys, completed_keys = rh._keys_from_reminders([reminder])

    # It must have read the ANCHOR, not the title -> the completed-key set carries the
    # create-side namespace key. Under the mutant (task_key(rem.name)) this set would
    # instead contain "acme - belege hochladen" and this assertion FAILS.
    assert anchor_key in completed_keys
    assert rh.task_key(human_title) not in completed_keys
    assert open_keys == set()

    # And end-to-end: those derived sets drive decide_create for a NEW candidate of the
    # SAME topic -> 'ask-repeat-of-done' (never a silent re-create). Under the mutant the
    # key wouldn't be in completed_keys -> decide_create returns 'create' -> the live
    # Acme/Beispielsteuer false-positive recurs, and this assertion FAILS.
    assert rh.decide_create(rh.task_key(topic), open_keys, completed_keys) == "ask-repeat-of-done"


def test_b1_cross_run_dedup_roundtrip_open_is_skip_open_dup():
    # B1: an OPEN live reminder for the same topic -> 'skip-open-dup' (no duplicate),
    # via the SAME persisted-anchor namespace on both sides.
    topic = "Beispielsteuer 2023"
    notes = rh.build_notes("Beispielsteuer zahlen.", date(2026, 6, 3),
                           "mailA", task_key=rh.task_key(topic))
    reminder = rh.Reminder(name="Finanzamt - Beispielsteuer", body=notes, completed=False)
    open_keys = {rh.parse_task_anchor(reminder.body)}
    assert rh.decide_create(rh.task_key(topic), open_keys, set()) == "skip-open-dup"


# --------------------------------------------------------------------------- #
# S1 — emitted notes preserve line structure ON-DEVICE.
# The false-green gap: build_notes returns multi-line notes, but create_reminder
# routed the whole body through _as_quote, which collapses control chars -> the
# on-device body became one line ("recapMail vom...[gmail:...]"). The KILLING test
# asserts on the EMITTED/quoted script (post-sanitize), NOT on build_notes output.
# Boundary feature: observed through the runner's own script-emit path via a _run
# override seam (no production code modified).
# --------------------------------------------------------------------------- #
class _CapturingRunner(rh.OsascriptRunner):
    """A real OsascriptRunner whose _run choke-point is overridden to CAPTURE the
    emitted AppleScript instead of shelling out — observes the POST-sanitize body."""

    def __init__(self):
        self.scripts = []

    def _run(self, script):                          # noqa: D401 — capture, don't exec
        self.scripts.append(script)
        return ""


def _emitted_body_segment(script: str) -> str:
    """Pull the body:"..." literal as it actually appears in the emitted script."""
    import re as _re
    m = _re.search(r'body:"(.*?)"\s*\}', script, _re.DOTALL)
    assert m, f"no body:\"...\" literal found in emitted script:\n{script}"
    return m.group(1)


def test_s1_emitted_body_keeps_gmail_id_on_its_own_final_line():
    # S1: the EMITTED (post-_as_quote) body must keep its line structure so the
    # AC-R5 [gmail:<id>] is on its own line on-device. Asserts on the emitted script,
    # not build_notes (that was the false-green). FAILS now because _as_quote strips
    # the newlines and the body collapses to a single line.
    runner = _CapturingRunner()
    body = rh.build_notes("Kurzer Recap.", date(2026, 6, 3), "abc123")
    runner.create_reminder(LIST, "Beispielbank - Belege", body)
    emitted = _emitted_body_segment(runner.scripts[0])
    # the emitted body must still be multi-line (line breaks preserved on-device):
    lines = emitted.splitlines()
    assert len(lines) >= 3, f"emitted body collapsed to one line: {emitted!r}"
    # [gmail:<id>] is on its OWN final line, not glued to the date:
    assert lines[-1] == "[gmail:abc123]"
    # the recap and the 'Mail vom ...' segment are separable (not concatenated):
    assert "Kurzer Recap." in lines
    assert "Mail vom 03.06.2026" in lines
    assert "Kurzer Recap.Mail vom 03.06.2026" not in emitted


def test_s1_emitted_body_separates_recap_from_mail_date():
    # S1: the 'Mail vom ...' segment must be separable from the recap on-device
    # (the collapse glued them: "recapMail vom ..."). Asserts on emitted form.
    runner = _CapturingRunner()
    body = rh.build_notes("Recap eins.", date(2026, 6, 4), "deadbeef")
    runner.create_reminder(LIST, "T", body)
    emitted = _emitted_body_segment(runner.scripts[0])
    assert "Recap eins.Mail vom" not in emitted       # not glued together
    assert "Mail vom 04.06.2026" in emitted.splitlines()


# --------------------------------------------------------------------------- #
# AC-R2 / injection: a due date must be bound NUMERICALLY in the emitted script
# (set year/month/day of theDue), and 'due date:theDue' must be attached to the
# reminder. The raw ISO string must NEVER be interpolated as a date value (that
# would be both wrong AppleScript and an injection vector). Observed on the
# emitted script via the _run capture seam.
# --------------------------------------------------------------------------- #
def test_emitted_due_date_is_bound_numerically_not_interpolated():
    runner = _CapturingRunner()
    runner.create_reminder(LIST, "T", "body\n[gmail:abc123]", due_date="2026-06-30")
    script = runner.scripts[0]
    assert "set year of theDue to 2026" in script
    assert "set month of theDue to 6" in script
    assert "set day of theDue to 30" in script
    assert "due date:theDue" in script                # attached to the reminder props
    assert '"2026-06-30"' not in script               # never interpolated as a literal


def test_emitted_script_has_no_due_block_when_no_deadline():
    # AC-R2: no invented deadline — without a due date the script binds no theDue and
    # attaches no 'due date:' property.
    runner = _CapturingRunner()
    runner.create_reminder(LIST, "T", "body\n[gmail:abc123]", due_date=None)
    script = runner.scripts[0]
    assert "theDue" not in script
    assert "due date:" not in script


# --------------------------------------------------------------------------- #
# S2 — empty/blank topic never silently merges (lost-task guard).
# Two distinct candidates both with topic="" (or whitespace) must NOT collapse
# into one 'skip-open-dup' (which silently drops the second = a lost task). A
# blank-topic candidate has no task identity -> recorded as an 'ask', never a
# silent skip/merge. PURE (plan_creations logic).
# --------------------------------------------------------------------------- #
def _blank_topic_candidate(topic, gmail_id, what):
    return rh.TaskCandidate(
        who="Absender", what=what, why=None, recap=f"Recap {what}.",
        mail_date=date(2026, 6, 3), gmail_id=gmail_id,
        topic=topic, explicit_deadline=None, filed_path=None, grey_area=False,
    )


def test_s2_two_blank_topic_candidates_both_surface_neither_lost():
    # S2: two DISTINCT candidates both with empty topic -> NEITHER is silently
    # dropped. Today they both task_key to "" -> the second becomes 'skip-open-dup'
    # and is silently lost. Corrected contract: blank topic = no identity -> ASK,
    # so BOTH surface (as asks), neither merged away.
    c1 = _blank_topic_candidate("", "mailX", "Aufgabe A")
    c2 = _blank_topic_candidate("", "mailY", "Aufgabe B")
    plan = rh.plan_creations([c1, c2], open_keys=set(), completed_keys=set())
    # neither task is lost: nothing silently skipped/merged.
    surfaced = len(plan["create"]) + len(plan["asks"])
    assert surfaced == 2, f"a blank-topic task was silently lost: {plan!r}"
    # specifically: a no-identity candidate is an ASK (not a silent create/merge).
    assert len(plan["asks"]) == 2


def test_s2_whitespace_topic_candidate_is_asked_not_silently_skipped():
    # S2: a whitespace-only topic likewise has no identity -> ASK, never a silent
    # 'skip-open-dup' merge with another no-identity task.
    c1 = _blank_topic_candidate("   ", "mailX", "Aufgabe A")
    c2 = _blank_topic_candidate("\t", "mailY", "Aufgabe B")
    plan = rh.plan_creations([c1, c2], open_keys=set(), completed_keys=set())
    assert len(plan["asks"]) == 2
    assert len(plan["create"]) == 0


def test_s2_blank_topic_does_not_collapse_a_real_open_task():
    # S2: a blank-topic candidate must not be swallowed by an unrelated open key
    # (it has no identity to match) -> it surfaces as an ask, the real task is
    # untouched. Guards against "" accidentally matching anything.
    c_blank = _blank_topic_candidate("", "mailZ", "Namenlose Aufgabe")
    real_open = rh.task_key("Echte offene Aufgabe")
    plan = rh.plan_creations([c_blank], open_keys={real_open}, completed_keys=set())
    assert len(plan["asks"]) == 1
    assert plan["create"] == []


# --------------------------------------------------------------------------- #
# N1 — whitespace-only title parts dropped (build_title filters on p and p.strip()).
# PURE.
# --------------------------------------------------------------------------- #
def test_n1_build_title_whitespace_parts_dropped_no_stray_separator():
    # N1: a whitespace-only 'who' and empty 'what' are dropped, no stray ' - '.
    t = rh.build_title("  ", "", None)
    assert " - " not in t
    assert t.strip() == t            # no leading/trailing whitespace artifacts
    assert t == ""                   # nothing supported -> empty title


def test_n1_build_title_all_none_is_empty():
    # N1: all parts None -> sensible empty handling, never " - " noise.
    assert rh.build_title(None, None, None) == ""


def test_n1_build_title_whitespace_who_dropped_keeps_rest():
    # N1: a whitespace 'who' is dropped but a real 'what'/'why' survive cleanly.
    t = rh.build_title("   ", "Umzug Firma", "Wegen Rückerstattung")
    assert t == "Umzug Firma - Wegen Rückerstattung"
    assert not t.startswith(" ") and not t.startswith("-")
