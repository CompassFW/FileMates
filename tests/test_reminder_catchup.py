"""Catch-up for the scheduled run (2026-06-08).

The host scheduler skips a slot if the Mac was asleep at that minute (it only catches up
"on next launch", not "on wake"). FileMates therefore ticks more often and gates each tick
with the pure `due_slots(now, schedule_times, last_run)` decision + a persisted last-success
timestamp: run the full (idempotent) flow iff a scheduled slot is overdue & unfulfilled.

These are pure-function table tests (mirroring test_reminder_schedule.py) plus state-file
round-trip/atomicity tests and check-catchup / record-run CLI integration through main().
"""
from datetime import datetime, timedelta, timezone

import reminder_helper as rh

TZ = timezone(timedelta(hours=2))          # a fixed CEST-like offset for the table tests
TIMES = [(9, 0), (18, 0)]                   # the configured slots


def dt(y, mo, d, h, mi, tz=TZ):
    return datetime(y, mo, d, h, mi, tzinfo=tz)


# --------------------------------------------------------------------------- #
# parse_schedule_times — robust normalisation, never raises on a typo
# --------------------------------------------------------------------------- #
def test_parse_times_from_raw_string():
    assert rh.parse_schedule_times("09:00, 18:00") == [(9, 0), (18, 0)]


def test_parse_times_from_list_of_strings():
    assert rh.parse_schedule_times(["09:00", "18:00"]) == [(9, 0), (18, 0)]


def test_parse_times_from_tuples_passthrough():
    assert rh.parse_schedule_times([(9, 0)]) == [(9, 0)]


def test_parse_times_single_digit_hour():
    assert rh.parse_schedule_times("9:00") == [(9, 0)]


def test_parse_times_skips_malformed_and_out_of_range():
    # "25:00" (hour), "09:61" (minute), "garbage" all dropped; only the valid one survives.
    assert rh.parse_schedule_times("garbage, 25:00, 09:61, 18:00") == [(18, 0)]


def test_parse_times_empty():
    assert rh.parse_schedule_times("") == []
    assert rh.parse_schedule_times(None) == []


# --------------------------------------------------------------------------- #
# due_slots — the pure gate. A slot today is due iff crossed AND not-run-since AND not-stale.
# --------------------------------------------------------------------------- #
def test_on_time_run_is_not_due_again():
    # ran at 09:01 today; at 09:02 nothing is due (09:00 already fulfilled, 18:00 not crossed).
    now = dt(2026, 6, 8, 9, 2)
    last = dt(2026, 6, 8, 9, 1)
    assert rh.due_slots(now, TIMES, last) == []


def test_single_missed_slot_is_due():
    # last run was yesterday evening; at 10:30 today the 09:00 slot is overdue.
    now = dt(2026, 6, 8, 10, 30)
    last = dt(2026, 6, 7, 18, 1)
    assert rh.due_slots(now, TIMES, last) == [dt(2026, 6, 8, 9, 0)]


def test_both_slots_missed_coalesce_to_list_but_flow_runs_once():
    # at 19:00, last run yesterday 22:00: BOTH 09:00 (10h, <12h) and 18:00 (1h) are due.
    now = dt(2026, 6, 8, 19, 0)
    last = dt(2026, 6, 7, 22, 0)
    due = rh.due_slots(now, TIMES, last)
    assert due == [dt(2026, 6, 8, 9, 0), dt(2026, 6, 8, 18, 0)]
    assert len(due) == 2          # caller still runs ONCE; length is only for the report


def test_before_first_slot_is_not_due():
    now = dt(2026, 6, 8, 8, 30)
    last = dt(2026, 6, 7, 18, 1)
    assert rh.due_slots(now, TIMES, last) == []


def test_first_ever_run_daytime_is_due():
    # no state file yet (last_run=None) → run during the day.
    now = dt(2026, 6, 8, 14, 0)
    assert rh.due_slots(now, TIMES, None) == [dt(2026, 6, 8, 9, 0)]


def test_first_ever_run_pre_dawn_waits():
    # last_run=None but pre-dawn: 09:00 not crossed yet, so nothing runs at 02:00.
    now = dt(2026, 6, 8, 2, 0)
    assert rh.due_slots(now, TIMES, None) == []


def test_staleness_just_inside_window_is_due():
    # only a 09:00 slot; at 20:30 it is 11.5h stale (< 12h) → still catch-up-eligible.
    now = dt(2026, 6, 8, 20, 30)
    assert rh.due_slots(now, [(9, 0)], None) == [dt(2026, 6, 8, 9, 0)]


def test_staleness_just_outside_window_is_skipped():
    # at 21:30 the 09:00 slot is 12.5h stale (> 12h) → not run (no late/pre-dawn catch-up).
    now = dt(2026, 6, 8, 21, 30)
    assert rh.due_slots(now, [(9, 0)], None) == []


def test_empty_schedule_times_is_never_due():
    now = dt(2026, 6, 8, 12, 0)
    assert rh.due_slots(now, [], dt(2026, 6, 1, 9, 0)) == []


def test_staleness_exact_boundary_is_still_due():
    # documented semantics: `now - slot <= max_staleness` — EXACTLY 12h is still due.
    now = dt(2026, 6, 8, 21, 0)
    assert rh.due_slots(now, [(9, 0)], None) == [dt(2026, 6, 8, 9, 0)]


def test_run_exactly_at_slot_time_fulfils_it():
    # documented semantics: `slot <= last_run` — a run recorded EXACTLY at the slot
    # time fulfils that slot (no re-run).
    now = dt(2026, 6, 8, 10, 0)
    last = dt(2026, 6, 8, 9, 0)                   # == the slot, not after it
    assert rh.due_slots(now, [(9, 0)], last) == []


def test_dst_spring_forward_slot_returned_once():
    # Wall-clock-local slots must survive a DST transition: exactly one 09:00 slot, no dup/skip.
    from zoneinfo import ZoneInfo                  # stdlib since 3.9 (CI floor is 3.11)
    berlin = ZoneInfo("Europe/Berlin")
    now = datetime(2026, 3, 29, 10, 0, tzinfo=berlin)      # spring-forward day, after 09:00
    last = datetime(2026, 3, 28, 18, 1, tzinfo=berlin)
    due = rh.due_slots(now, [(9, 0)], last)
    assert len(due) == 1 and due[0].hour == 9


# --------------------------------------------------------------------------- #
# State file — atomic write, tolerant read (corrupt/naive/missing → None).
# --------------------------------------------------------------------------- #
def test_state_roundtrip_preserves_aware_offset(tmp_path):
    p = tmp_path / ".filemates-last-run.local.json"
    ts = dt(2026, 6, 8, 9, 1)
    rh.write_last_run(p, ts)
    got = rh.read_last_run(p)
    assert got == ts
    assert got.utcoffset() == ts.utcoffset()      # offset round-trips, not just the instant


def test_state_missing_file_is_none(tmp_path):
    assert rh.read_last_run(tmp_path / "nope.json") is None


def test_state_corrupt_json_is_none_no_raise(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert rh.read_last_run(p) is None


def test_state_naive_timestamp_is_rejected(tmp_path):
    # a timezone-naive timestamp is ambiguous → treated as corrupt (None → fail toward running).
    p = tmp_path / "naive.json"
    p.write_text('{"last_success": "2026-06-08T09:01:00"}', encoding="utf-8")
    assert rh.read_last_run(p) is None


def test_state_write_is_atomic_no_tmp_left(tmp_path):
    p = tmp_path / ".filemates-last-run.local.json"
    rh.write_last_run(p, dt(2026, 6, 8, 9, 1))
    assert p.exists()
    assert list(tmp_path.glob("*.tmp")) == []     # no partial/temp file lingers


def test_state_write_crash_leaves_old_value_intact(monkeypatch, tmp_path):
    # THE atomicity invariant (kills the direct-write mutant): a crash at the
    # replace step must leave the PREVIOUS state readable — never a torn/empty file.
    # A non-atomic `p.write_text(...)` implementation would not call os.replace at
    # all (no exception) and would already have overwritten the live file.
    import pytest
    p = tmp_path / ".filemates-last-run.local.json"
    old = dt(2026, 6, 7, 18, 1)
    rh.write_last_run(p, old)

    def boom(src, dst):
        raise OSError("simulated crash between write and replace")

    monkeypatch.setattr(rh.os, "replace", boom)
    with pytest.raises(OSError):
        rh.write_last_run(p, dt(2026, 6, 8, 9, 1))
    assert rh.read_last_run(p) == old             # old value fully intact


def test_state_write_overwrites_existing(tmp_path):
    p = tmp_path / ".filemates-last-run.local.json"
    rh.write_last_run(p, dt(2026, 6, 7, 9, 1))
    rh.write_last_run(p, dt(2026, 6, 8, 9, 1))
    assert rh.read_last_run(p) == dt(2026, 6, 8, 9, 1)


# --------------------------------------------------------------------------- #
# CLI integration through main() — exit 10 = DUE, 0 = NOOP; record-run advances state.
# --------------------------------------------------------------------------- #
def _write_cfg(tmp_path, enabled="yes", times="09:00, 18:00"):
    cfg = tmp_path / "config.local.md"
    cfg.write_text(
        "## Schedule\n"
        f"- `schedule_enabled:` {enabled}\n"
        f"- `schedule_times:` {times}\n",
        encoding="utf-8",
    )
    return cfg


def test_cli_noop_when_fresh(tmp_path):
    cfg = _write_cfg(tmp_path)
    state = tmp_path / "s.json"
    rh.write_last_run(state, dt(2026, 6, 8, 9, 1))
    rc = rh.main(["check-catchup", "--config", str(cfg), "--state", str(state),
                  "--now", "2026-06-08T09:02:00+02:00"])
    assert rc == 0


def test_cli_due_when_backdated(tmp_path):
    cfg = _write_cfg(tmp_path)
    state = tmp_path / "s.json"
    rh.write_last_run(state, dt(2026, 6, 5, 18, 1))           # days ago
    rc = rh.main(["check-catchup", "--config", str(cfg), "--state", str(state),
                  "--now", "2026-06-08T10:30:00+02:00"])
    assert rc == 10                                           # DUE


def test_cli_record_run_then_noop(tmp_path):
    cfg = _write_cfg(tmp_path)
    state = tmp_path / "s.json"
    # initially no state → DUE
    assert rh.main(["check-catchup", "--config", str(cfg), "--state", str(state),
                    "--now", "2026-06-08T10:30:00+02:00"]) == 10
    # record a success AT 10:30 → the 09:00 slot is now fulfilled → NOOP at 10:31
    rh.main(["record-run", "--state", str(state), "--at", "2026-06-08T10:30:00+02:00"])
    assert rh.main(["check-catchup", "--config", str(cfg), "--state", str(state),
                    "--now", "2026-06-08T10:31:00+02:00"]) == 0


def test_cli_failed_run_without_record_stays_due(tmp_path):
    # A run that crashed (never called record-run) must leave the state so the next tick retries.
    cfg = _write_cfg(tmp_path)
    state = tmp_path / "s.json"          # never written = simulated failed/never-recorded run
    assert rh.main(["check-catchup", "--config", str(cfg), "--state", str(state),
                    "--now", "2026-06-08T10:30:00+02:00"]) == 10


def test_cli_disabled_schedule_is_noop(tmp_path):
    cfg = _write_cfg(tmp_path, enabled="no")
    state = tmp_path / "s.json"
    assert rh.main(["check-catchup", "--config", str(cfg), "--state", str(state),
                    "--now", "2026-06-08T10:30:00+02:00"]) == 0


def test_cli_no_times_is_noop(tmp_path):
    cfg = _write_cfg(tmp_path, times="")
    state = tmp_path / "s.json"
    assert rh.main(["check-catchup", "--config", str(cfg), "--state", str(state),
                    "--now", "2026-06-08T10:30:00+02:00"]) == 0


def test_cli_check_catchup_makes_no_state_mutation(tmp_path):
    # The gate must be read-only: a NOOP/DUE check never creates or changes the state file.
    cfg = _write_cfg(tmp_path)
    state = tmp_path / "s.json"          # does not exist
    rh.main(["check-catchup", "--config", str(cfg), "--state", str(state),
             "--now", "2026-06-08T10:30:00+02:00"])
    assert not state.exists()            # gate created nothing
