#!/usr/bin/env python3
"""FileMates reminder-helper — the *tested* core of the To-Do feature.

Why this exists (replaces agent-prose AppleScript): the To-Do feature is safety-touching
(it mutates the user's Apple Reminders AND archives mail) and had three real past failures
that MUST never recur — they are encoded here as deterministic, falsifiable behaviour:

  AC-R1  check-off sorts ONLY the one [gmail:<id>]-linked mail (never bulk); 0 or >1 → ASK.
  AC-R2  never invent a deadline — a due date is set only from an explicitly named one.
  AC-R3  no duplicate reminder — TASK-level dedup over open+completed+overdue; repeat-of-done → ASK.
  AC-R4  title = `Wer - Was - Warum`; missing parts dropped (never invented); grey-area `Review:`; id never in title.
  AC-R5  notes = recap · `Mail vom TT.MM.JJJJ` · `gefilt: <path>` (iff filed) · `[gmail:<id>]` as the LAST line.
  AC-R6  idempotency/recovery: double-completion / already-archived → safe no-op.
  AC-R7  one mail → multiple to-dos, ONE task each (no bundling).
  AC-R8  completion signal = the user's check-off ONLY (never mail-inferred).
  AC-R1×R7  a mail is sorted only once ALL its to-dos are checked off.
  AC-R9  one dedicated list (default `Email-Tasks`); other lists / manual reminders untouched.

ARCHITECTURE — three injectable, fakeable seams so the whole core is deterministic in CI
(mirrors decide_trash / plan_expunge / RecordingIMAP in fetch-attachments.py):

    pure decision  ↔  MailResolver  ↔  OsascriptRunner
    (no I/O)          (gmail-id →      (Apple Reminders via
                       {0|1|n} mails;   osascript; owns ALL
                       sorts ONE mail)  AppleScript generation)

The historical "archived ALL mails" bug lived in the *selection/execution* layer, not the
decision — so the seam-contract tests (S1) assert the runner/resolver receives EXACTLY the
intended command targeting a SINGLE EXPLICIT id, never a `whose`/label predicate that could
over-match. "No bulk command" is necessary but not sufficient.

HONEST EVIDENCE BOUNDARY (Reality Ledger): the deterministic decision core + all three seam
contracts are CI-testable here. The END-TO-END effects — a real Gmail-MCP resolve of a hex
id to exactly one mail, a real Reminders check-off round-trip, and `[gmail:<id>]` surviving
create→edit→iCloud-sync→complete — are `integration-fake` (RED) until the one human-in-loop
smoke. They are NOT claimed green by this file.

AppleScript guardrails (verified live, confined to OsascriptRunner — never built elsewhere):
no reserved/abbreviation tokens as variables (`at`, `st`, `in`, `id`, `date`, `name`),
NO inline `if…then…else` expression; German umlauts round-trip fine.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

__version__ = "0.2.0"  # x-release-please-version

# The link anchor written into a reminder's notes. The real Gmail-API message id is lowercase
# hex (of X-GM-MSGID), but the token charset is the wider alphanumeric run so the anchor stays
# robust to any id shape the connector hands back; it must NOT contain whitespace/punctuation
# (those make the token malformed → parse_token returns None → caller asks). The token must be
# the mandatory LAST line of the notes (AC-R5).
TOKEN_RE = re.compile(r"\[gmail:([0-9A-Za-z]+)\]")

# The persisted TASK-identity anchor (B1). Unlike the gmail id, a task key comes from
# task_key(topic) — it lowercases + collapses whitespace, so a key legitimately contains
# spaces and unicode letters. The anchor therefore captures everything up to the closing `]`
# on the same line (no `]` or newline inside a key). It sits immediately before the
# mandatory-last `[gmail:<id>]` line so the read-back dedup key shares the create-side
# namespace (task_key(topic)) — closing the cross-run dedup blindness (a recurring vendor invoice).
TASK_RE = re.compile(r"\[task:([^\]\n]+)\]")


# ─────────────────────────────────────────────────────────────────────────────
# Data shapes (plain dataclasses — no behaviour, just typed records)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Reminder:
    """A reminder as read back from the Email-Tasks list."""
    name: str
    body: str
    completed: bool
    due_date: Optional[str] = None


@dataclass
class TaskCandidate:
    """One actionable task extracted from a mail by the LLM (AC-R7: ONE per task).
    The helper enforces the *mechanical* guarantees; the LLM supplies the content."""
    who: Optional[str]
    what: Optional[str]
    why: Optional[str]
    recap: str
    mail_date: date
    gmail_id: str
    # AC-R3: the canonical TASK identity (LLM-supplied), used for task-level dedup. The
    # same real task across many mails must carry the same `topic` so it collapses to one
    # key. dedup keys on `task_key(topic)`, NOT on who/what/why or the gmail-id.
    topic: str = ""
    explicit_deadline: Optional[date] = None
    filed_path: Optional[str] = None
    grey_area: bool = False


@dataclass
class ReminderSpec:
    """A fully-formed reminder ready to be created via the runner."""
    title: str
    notes: str
    due_date: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# PURE DECISION LAYER (no I/O — unit-testable in isolation)
# ─────────────────────────────────────────────────────────────────────────────
def parse_token(body: str) -> Optional[str]:
    """Extract the gmail id from a single `[gmail:<id>]` token in the notes.

    Returns the id iff EXACTLY ONE well-formed token is present. Missing, malformed,
    user-edited, or multiple/ambiguous tokens → None (caller MUST ask, never fuzzy-match,
    never act on a different mail). [S2-minimal degrade-safe anchor]
    """
    matches = TOKEN_RE.findall(body or "")
    if len(matches) != 1:                 # 0 (missing/malformed) or ≥2 (ambiguous) → ask
        return None
    return matches[0]


def parse_task_anchor(body: str) -> Optional[str]:
    """B1. Extract the persisted task key from a single `[task:<key>]` anchor in the notes.

    Returns the key iff EXACTLY ONE well-formed anchor is present. Missing, user-edited, or
    multiple/ambiguous anchors → None (caller must ask, never guess the task identity).
    Mirrors parse_token; the read-back key here is the SAME namespace as task_key(topic)
    written at create time, so cross-run task-level dedup actually matches (B1)."""
    matches = TASK_RE.findall(body or "")
    if len(matches) != 1:                 # 0 (missing) or ≥2 (ambiguous) → ask
        return None
    return matches[0]


def is_filemates_reminder(body: str) -> bool:
    """True iff the reminder carries a well-formed `[gmail:<id>]` token. Manual reminders
    (no token) are OFF-LIMITS — never read for action, never modified, never deleted."""
    return parse_token(body) is not None


def decide_mail_action(resolved_count: int, label_mapped: bool) -> str:
    """AC-R1 + S2-minimal. Returns 'sort' iff EXACTLY ONE mail resolved AND a label is
    mapped for it; otherwise 'ask'. (count 0 → ask; count ≥2 → ask; no label → ask.)
    The single load-bearing guard against the historical bulk-archive bug."""
    return "sort" if (resolved_count == 1 and label_mapped) else "ask"


def decide_due_date(explicit_deadline: Optional[date]) -> Optional[str]:
    """AC-R2. Returns the deadline as an ISO `YYYY-MM-DD` string ONLY when the mail
    explicitly named a deadline; otherwise None. Never invents/derives a deadline.
    (ISO keeps the pure layer format-stable + testable; OsascriptRunner converts ISO →
    an AppleScript date when creating the reminder.)"""
    if explicit_deadline is None:
        return None
    return explicit_deadline.isoformat()


def build_title(who: Optional[str], what: Optional[str], why: Optional[str],
                grey_area: bool = False) -> str:
    """AC-R4. `Wer - Was - Warum`. A part the mail does not support is None and is DROPPED
    (never invented). grey_area → prefix `Review: `. The `[gmail:<id>]` is NEVER in the title."""
    # Drop any None / empty / whitespace-only part (never invent), and strip each kept part
    # so a blank-but-present field can't inject a stray ` - ` separator (N1).
    parts = [p.strip() for p in (who, what, why) if p and p.strip()]
    title = " - ".join(parts)
    if grey_area:
        title = "Review: " + title
    return title


def format_mail_date(d: date) -> str:
    """AC-R5 helper. The mail's real date as `Mail vom TT.MM.JJJJ`."""
    return f"Mail vom {d.day:02d}.{d.month:02d}.{d.year}"


def build_notes(recap: str, mail_date: date, gmail_id: str,
                task_key: Optional[str] = None, filed_path: Optional[str] = None) -> str:
    """AC-R5 (revised, B1). Notes in order: (1) recap, (2) `Mail vom TT.MM.JJJJ`,
    (3) `gefilt: <path>` IFF an attachment was filed, (4) `[task:<key>]` IFF a task_key is
    given, (5) `[gmail:<id>]` as the mandatory LAST line. No invented facts. The id lives
    here, never in the title (AC-R4). When task_key is None this is exactly the pre-B1 shape
    (no anchor, id last) — back-compat. The `[task:<key>]` anchor persists the create-time
    task identity so the next run reads it back in the SAME namespace as task_key(topic),
    making cross-run task-level dedup work (B1)."""
    lines = [recap, format_mail_date(mail_date)]
    if filed_path:                                  # only when an attachment was actually filed
        lines.append(f"gefilt: {filed_path}")
    if task_key:                                    # persist task identity, immediately before id
        lines.append(f"[task:{task_key}]")
    lines.append(f"[gmail:{gmail_id}]")             # mandatory LAST line (the link anchor)
    return "\n".join(lines)


def task_key(topic: str) -> str:
    """AC-R3 (revised). A normalized TASK-identity key (not the gmail-id): the same real
    task arriving across many mails must collapse to one key, so dedup is task-level.
    Normalizes case/whitespace; stable for the same topic."""
    return " ".join((topic or "").split()).casefold()


def decide_create(key: str, open_keys: set, completed_keys: set) -> str:
    """AC-R3 (revised). Task-level dedup across ALL states. Returns:
      'create'             — key unseen in open and completed → make the reminder.
      'skip-open-dup'      — key has an OPEN reminder → do nothing (incl. overdue). OPEN WINS.
      'ask-repeat-of-done' — key is ONLY in completed (not currently open) → ASK, never re-create.
    Any uncertainty about 'same task' resolves toward asking, never silent merge/create.

    Precedence: OPEN wins over completed. A key that is in BOTH sets (an artifact of legacy
    duplication, surfaced by the live smoke) is an ACTIVELY OPEN task → skip silently, don't nag;
    a prior completion of a now-open task is not a genuine repeat-of-done. The ask-branch is
    reserved for a task that was done and is NOT currently open (e.g. a recurring obligation
    arriving again after the previous one was checked off)."""
    if key in open_keys:                            # already live (incl. overdue) → no duplicate
        return "skip-open-dup"
    if key in completed_keys:                       # done before AND not open now → never silent re-create
        return "ask-repeat-of-done"
    return "create"


def all_siblings_done(gmail_id: str, reminders: list) -> bool:
    """AC-R1×R7. True iff EVERY reminder linked to this gmail_id is completed. A mail with
    several to-dos is sortable only once the LAST is checked off — never while one is open."""
    siblings = [r for r in reminders if parse_token(r.body) == gmail_id]
    if not siblings:                                # no linked reminder → nothing to gate on
        return False
    return all(r.completed for r in siblings)


def decide_idempotent_action(already_archived: bool) -> str:
    """AC-R6 (decision part). 'noop' if the linked mail is already archived/acted-upon,
    else 'sort'. Acting twice must be a safe no-op (no double-archive, no error)."""
    return "noop" if already_archived else "sort"


def decide_unattended(action: str, mode: str) -> str:
    """Unattended-run policy (user-configurable schedule). Returns 'execute' iff a scheduled
    (no-human-present) run may perform `action` itself in `mode`, else 'queue' (defer for the
    user to confirm when present). The single source of truth a scheduled run applies.

      action:
        'create'     — make a reminder (additive, safe).
        'sort'       — reversible label + archive (remove INBOX).
        'trash-rule' — move to Trash (reversible, 30-day) for a mail the USER's standing
                       delete-rule already pre-authorizes (sender/category on the delete-list,
                       protection gate passed). The rule IS the pre-authorization — that's its
                       whole point, so it runs without re-asking.
        'delete'     — any deletion NOT covered by a standing rule: agent-suspected junk, an
                       ad-hoc one-off, or a permanent expunge. Always needs the human (ask →
                       and if confirmed "always from this sender", it becomes a rule → next
                       time it's 'trash-rule').
      mode:
        'attended'    — human present → everything executes.
        'auto-sort'   — create + sort + rule-covered trash execute; ad-hoc DELETE queues.
        'collect'     — create executes (additive); any mail move/trash queues.
        'report-only' — nothing executes; everything is reported/queued.

    Fail-safe: an unknown mode/action returns 'queue' — never auto-execute on a typo'd config.
    Ad-hoc/permanent DELETE never executes unattended (only 'attended'); protected senders are
    excluded upstream (protection gate) and never reach 'trash-rule'."""
    allow = {
        "attended": {"create", "sort", "trash-rule", "delete"},
        "auto-sort": {"create", "sort", "trash-rule"},
        "collect": {"create"},
        "report-only": set(),
    }
    return "execute" if action in allow.get(mode, set()) else "queue"


# ─────────────────────────────────────────────────────────────────────────────
# CATCH-UP for the scheduled run (pure layer) — the host scheduler skips a slot if
# the machine was asleep at that minute (only "on next launch" is caught up, not "on
# wake"). So we tick more often and gate each tick with this pure decision + a persisted
# last-success timestamp: run the full (idempotent) flow iff a slot is overdue & unfulfilled.
# ─────────────────────────────────────────────────────────────────────────────
MAX_STALENESS_HOURS = 12  # how long a missed slot stays catch-up-eligible (no pre-dawn run of a stale slot)


def parse_schedule_times(raw) -> list:
    """Pure. Normalise schedule times to a list of (hour, minute) tuples. Accepts a raw
    config string ('09:00, 18:00'), or a list of 'HH:MM' strings / (h,m) tuples. Malformed
    or out-of-range entries are skipped (never raises) — a typo can't crash the gate."""
    items = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    out: list = []
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                h, mi = int(item[0]), int(item[1])
            except (TypeError, ValueError):     # non-numeric tuple: skip, keep the contract
                continue
        else:
            m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(item))
            if not m:
                continue
            h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            out.append((h, mi))
    return out


def due_slots(now: datetime, schedule_times, last_run: Optional[datetime],
              max_staleness_hours: int = MAX_STALENESS_HOURS) -> list:
    """Pure. Which of TODAY's scheduled slots are DUE & unfulfilled at `now` — i.e. should a
    catch-up tick run the full FileMates flow? Returns the sorted list of due slot datetimes
    (run the flow exactly ONCE if non-empty; the list length is only for the report/tests).

      now:            aware datetime (local).
      schedule_times: '09:00, 18:00' | ['09:00','18:00'] | [(9,0),(18,0)].
      last_run:       aware datetime of the last SUCCESSFUL run, or None (never ran / corrupt).

    A slot today is due iff ALL hold:
      (1) now >= slot                         — the slot time today has been crossed
      (2) last_run is None or slot > last_run — we have NOT already run since this slot
      (3) now - slot <= max_staleness         — staleness guard: a slot crossed long ago is
                                                not run on a much-later wake (e.g. pre-dawn)
    Multiple missed slots may be returned; the caller still runs ONCE (a full inbox scan
    covers them all). last_run=None => condition (2) always holds, but (3) still applies, so a
    fresh install runs during the day and waits pre-dawn. Slots are wall-clock-local
    (conditions 1+3 are DST-safe). KNOWN, ACCEPTED edge: on the autumn fall-back day,
    condition (2) compares absolute instants, so a slot fulfilled shortly before the clock
    change can be reported due once more (~1h offset) -> at most ONE extra idempotent run
    per year, harmless by design."""
    staleness = timedelta(hours=max_staleness_hours)
    due: list = []
    for (h, mi) in parse_schedule_times(schedule_times):
        slot = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if now < slot:                                  # (1) not crossed yet today
            continue
        if last_run is not None and slot <= last_run:   # (2) already ran since this slot
            continue
        if now - slot > staleness:                      # (3) too stale to catch up now
            continue
        due.append(slot)
    return sorted(due)


# ── catch-up I/O (kept OUT of the pure layer above) ──────────────────────────
_TOOLS_DIR = Path(__file__).resolve().parent


def _skill_dir() -> Path:
    """Find the filemates skill dir in both layouts (repo: tools + skills/filemates;
    installed: ~/.claude/skills/filemates/tools). Mirrors fetch-attachments._skill_dir."""
    for c in (_TOOLS_DIR.parent / "skills" / "filemates", _TOOLS_DIR.parent, _TOOLS_DIR):
        if (c / "config.local.md").exists() or (c / "config.example.md").exists():
            return c
    return _TOOLS_DIR.parent / "skills" / "filemates"


DEFAULT_CONFIG = _skill_dir() / "config.local.md"
# last successful scheduled run; gitignored (*.local.json). Lives at the repo/skill root.
STATE_FILE = _TOOLS_DIR.parent / ".filemates-last-run.local.json"


def read_schedule_config(path: Path) -> tuple:
    """Read (schedule_enabled: bool, schedule_times_raw: str) from the markdown config.
    DELIBERATELY duplicated mini-reader (12 lines) instead of importing the downloader's
    parse_config: the hyphenated filename would need an importlib dance (as conftest.py
    does for tests), and the catch-up gate must stay import-light and unable to break
    when the downloader changes. Only these two trivial `- \\`key:\\` value <!-- comment -->`
    keys are read; any richer config parsing belongs to parse_config."""
    enabled, times_raw = False, ""
    p = Path(path)
    if not p.exists():
        return enabled, times_raw
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*-?\s*`?([A-Za-z_]+)`?\s*:\s*`?(.*)", line)
        if not m:
            continue
        key = m.group(1).lower()
        val = re.sub(r"<!--.*?-->", "", m.group(2)).strip().strip("`").strip()
        if key == "schedule_enabled":
            enabled = val.lower() in ("yes", "true", "on", "1")
        elif key == "schedule_times":
            times_raw = val
    return enabled, times_raw


def read_last_run(path) -> Optional[datetime]:
    """The last SUCCESSFUL-run timestamp (aware datetime) or None. Missing / unreadable /
    malformed / timezone-naive → None, which makes the gate fail TOWARD running (the safe,
    idempotent direction). Never raises."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(str(data["last_success"]))
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"reminder-helper: ignoring unreadable state file {p} ({e})", file=sys.stderr)
        return None
    if ts.tzinfo is None:
        # same observability as the corrupt case: say WHY the state is being ignored.
        print(f"reminder-helper: ignoring timezone-naive timestamp in {p}", file=sys.stderr)
        return None
    return ts


def write_last_run(path, ts: datetime) -> None:
    """Atomically persist the last successful-run timestamp (write tmp + os.replace, so the
    live file is never partially written). Call ONLY after a fully successful run."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"last_success": ts.isoformat()}), encoding="utf-8")
    os.replace(tmp, p)


# ─────────────────────────────────────────────────────────────────────────────
# SEAMS (injectable boundaries — real impls do I/O; fakes record + return canned)
# ─────────────────────────────────────────────────────────────────────────────
def _as_quote(value: str) -> str:
    """Quote an arbitrary Python string as a SAFE AppleScript string literal — the security
    boundary for ANY value interpolated into a generated script (a reminder title/notes must
    never be able to break out of the script). Mirrors how trash_one() quote-sanitizes the
    folder name: backslashes and double-quotes are escaped, control chars (incl. the newline
    that would otherwise terminate the statement) are stripped. German umlauts pass through."""
    text = "" if value is None else str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    # Drop raw C0 control characters (newline/tab/US/RS/etc.) that could terminate or reshape
    # the statement, or corrupt the list_reminders field-separated parse. The dead `\xa0`
    # clause was redundant (U+00A0 is already >= " "). U+2028/U+2029/U+0085 (unicode line/
    # paragraph separators, NEL) are intentionally NOT stripped: they are ordinary printable
    # codepoints to AppleScript, not statement terminators, and may occur in real pasted text.
    text = "".join(ch for ch in text if ch >= " ")
    return '"' + text + '"'


def _quote_multiline(value: str) -> str:
    """S1. Quote a (possibly multi-line) notes body as ONE AppleScript string literal whose
    lines are preserved ON-DEVICE.

    The earlier code routed the whole body through _as_quote, whose control-char strip
    collapsed every newline → the on-device notes became a single glued line and the AC-R5
    `[gmail:<id>]` no longer sat on its own line. Here each line is sanitized independently
    (via _as_quote — the same escape/strip security boundary, so a `"`/`do shell script`
    payload still can't break out), then the sanitized line CONTENTS are rejoined with a real
    linefeed inside a single pair of quotes. A literal newline inside an AppleScript `"…"`
    literal is accepted by `osascript -e` and round-trips as a real linefeed on-device
    (verified), so the human-readable AC-R5 structure actually holds in Reminders while the
    string stays a single, breakout-safe literal."""
    # _as_quote returns each line wrapped in quotes; strip the wrapping (first/last char) to
    # get the sanitized inner content, then join the contents with a real newline and re-wrap
    # once. Splitting on "\n" first means each piece is newline-free, so _as_quote's newline
    # strip never touches legitimate content here.
    inner_lines = [_as_quote(line)[1:-1] for line in (value or "").split("\n")]
    return '"' + "\n".join(inner_lines) + '"'


def _parse_iso_due(iso_due: Optional[str]) -> Optional[datetime]:
    """Validate an ISO `YYYY-MM-DD` due date and return it as a datetime, or None for no/invalid
    date. Validating the shape HERE means a malformed value can never reach the generated
    AppleScript (the OsascriptRunner builds the date numerically from year/month/day, never by
    interpolating user text)."""
    if not iso_due:
        return None
    try:
        return datetime.strptime(iso_due, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None                                  # never emit a phantom/garbled date


class OsascriptRunner:
    """Real boundary to Apple Reminders. Owns ALL AppleScript generation (the only place
    reserved-word/inline-if landmines can occur). `integration-fake` until the human smoke."""

    LIST_VAR = "theList"
    DUE_VAR = "theDue"

    def _run(self, script: str) -> str:
        """Execute an AppleScript via osascript; raise on failure. Single choke-point so every
        generated script goes through the same boundary."""
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"osascript failed (rc={proc.returncode}): {proc.stderr.strip()}")
        return proc.stdout.strip()

    def _due_block(self, iso_due: Optional[str]):
        """AppleScript lines that bind `theDue` to the ISO due date (or None if no due date).
        Builds the date numerically (year/month/day setters) — no user text, no reserved var,
        no inline if/then/else."""
        parsed = _parse_iso_due(iso_due)
        if parsed is None:
            return None
        return [
            f"set {self.DUE_VAR} to (current date)",
            f"set year of {self.DUE_VAR} to {parsed.year}",
            f"set month of {self.DUE_VAR} to {parsed.month}",
            f"set day of {self.DUE_VAR} to {parsed.day}",
            f"set hours of {self.DUE_VAR} to 9",
            f"set minutes of {self.DUE_VAR} to 0",
            f"set seconds of {self.DUE_VAR} to 0",
        ]

    def ensure_list(self, list_name: str) -> None:
        """AC-R9. Create the dedicated list iff absent; never touch other lists. Uses a
        guarded `if not (exists …)` block (a statement, not an inline-if expression)."""
        quoted = _as_quote(list_name)
        script = "\n".join([
            'tell application "Reminders"',
            f"    if not (exists list {quoted}) then",
            f"        make new list with properties {{name:{quoted}}}",
            "    end if",
            "end tell",
        ])
        self._run(script)

    def list_reminders(self, list_name: str) -> list:
        """Return [Reminder, …] for the given list (open AND completed — dedup needs both).

        SEC-2: the BODY is emitted as the TRAILING field and parsed with a CAPPED split, so a
        body that itself contains the field separator (US) stays intact in the last field and
        can never shift the `completed` flag. Per-record layout: name<US>completed<US>body<RS>,
        records separated by RS, parsed with split(US, 2). US (0x1f) / RS (0x1e) are control
        chars; _as_quote strips them from any value the helper itself writes, but the body is
        attacker-influenced (parsed-mail text), so the trailing-field + capped-split layout is
        what actually guarantees robustness, not the sanitizer alone."""
        quoted = _as_quote(list_name)
        unit, rec = chr(0x1F), chr(0x1E)
        script = "\n".join([
            "set theUnit to (ASCII character 31)",
            "set theRec to (ASCII character 30)",
            'set theOut to ""',
            'tell application "Reminders"',
            # A not-yet-created list must degrade to "no reminders", never a -1728 crash
            # (S-3 robustness: dry-run / first run before the list exists).
            f"    if exists list {quoted} then",
            f"        set theItems to every reminder in list {quoted}",
            "        repeat with theItem in theItems",
            '            set theName to (name of theItem as string)',
            '            set theDone to (completed of theItem as string)',
            '            set theBody to (body of theItem as string)',
            "            set theOut to theOut & theName & theUnit & theDone"
            " & theUnit & theBody & theRec",
            "        end repeat",
            "    end if",
            "end tell",
            "return theOut",
        ])
        raw = self._run(script)
        reminders: list = []
        for record in raw.split(rec):
            if not record:
                continue
            # Capped split: at most 2 splits → [name, completed, body]. A US inside the body
            # is left untouched in the trailing field, so the flag is never shifted (SEC-2).
            fields = record.split(unit, 2)
            if len(fields) < 3:
                continue
            name, done, body = fields[0], fields[1], fields[2]
            reminders.append(Reminder(
                name=name, body=body,
                completed=done.strip().lower() == "true",
            ))
        return reminders

    def create_reminder(self, list_name: str, name: str, body: str,
                        due_date: Optional[str] = None) -> None:
        """Create one reminder in the dedicated list. Title/notes are passed through
        _as_quote (the security boundary); the optional due date is built numerically."""
        quoted_list = _as_quote(list_name)
        props = [f"name:{_as_quote(name)}", f"body:{_quote_multiline(body)}"]
        due_lines = self._due_block(due_date)
        lines = ['tell application "Reminders"',
                 f"    set {self.LIST_VAR} to list {quoted_list}"]
        if due_lines is not None:
            lines += ["    " + ln for ln in due_lines]
            props.append(f"due date:{self.DUE_VAR}")
        lines.append(
            "    make new reminder at end of "
            f"{self.LIST_VAR} with properties {{{', '.join(props)}}}"
        )
        lines.append("end tell")
        self._run("\n".join(lines))


class MailResolver:
    """Real boundary to the mail store (Gmail MCP, Claude-mediated). gmail-id → {0|1|n}
    mails, and a single-explicit-id sort. The COUNT it returns drives the ask-branch.
    `integration-fake` until the human smoke.

    The actual Gmail-MCP calls are Claude-mediated (the agent issues search/label/archive via
    the MCP connector), so this thin shell records the intent the CLI must carry out and keeps
    the single-explicit-id contract: resolve(id) reports the match count for ONE hex id, and
    sort(id, label) addresses EXACTLY that one id — never a `whose`/label predicate."""

    def __init__(self, lookup=None, sorter=None):
        # Injectable callables wire the real Gmail-MCP side (kept out of the deterministic core
        # so CI exercises the contract via fakes). Default no-op shells make instantiation safe.
        self._lookup = lookup
        self._sorter = sorter

    def resolve(self, gmail_id: str) -> int:
        """Return how many mails the hex id resolves to (0, 1, or n). Defers to the injected
        Gmail-MCP lookup; with none wired it reports 0 (→ ask, the safe default)."""
        if self._lookup is None:
            return 0
        return int(self._lookup(gmail_id))

    def sort(self, gmail_id: str, label: str) -> bool:
        """Sort away EXACTLY the one mail addressed by this explicit id (apply mapped label
        + archive). Must NEVER use a `whose`/label predicate that could match other mails."""
        if self._sorter is None:
            return False
        return bool(self._sorter(gmail_id, label))


# ─────────────────────────────────────────────────────────────────────────────
# WIRED ORCHESTRATION (decision + seams; tested via fakes — the seam-contract path)
# ─────────────────────────────────────────────────────────────────────────────
def react_to_checkoffs(runner: OsascriptRunner, resolver: MailResolver,
                       list_name: str, label_map: dict) -> dict:
    """AC-R1, R6, R8, R1×R7. React to reminders the USER checked off (the ONLY done signal).

    For each COMPLETED FileMates reminder (has a token) whose mail has ALL siblings done:
      parse_token → resolver.resolve(id) → decide_mail_action(count, label_mapped)
        'sort' → resolver.sort(id, label)  (called once, explicit id — never bulk)
        'ask'  → recorded in the report, no mail touched.
    Manual reminders (no token) are never read for action. Returns
    {'sorted': [...], 'asks': [...]}.
    """
    reminders = runner.list_reminders(list_name)
    report: dict = {"sorted": [], "asks": []}
    seen: set = set()                               # one decision per mail id (siblings collapse)

    for rem in reminders:
        if not rem.completed:                       # AC-R8: only the user's check-off is "done"
            continue
        gmail_id = parse_token(rem.body)            # manual / ambiguous reminders → None → skip
        if gmail_id is None or gmail_id in seen:
            continue
        # AC-R1×R7: act only once the LAST sibling on this mail is checked off.
        if not all_siblings_done(gmail_id, reminders):
            continue
        seen.add(gmail_id)

        count = resolver.resolve(gmail_id)          # the COUNT drives the ask-branch (AC-R1)
        label = label_map.get(gmail_id)
        action = decide_mail_action(count, label is not None)
        if action == "sort":
            # EXACTLY one explicit-id sort — never a bulk/predicate call (the bug guard).
            resolver.sort(gmail_id, label)
            report["sorted"].append(gmail_id)
        else:
            report["asks"].append(gmail_id)
    return report


def open_gmail_ids_from_reminders(reminders: list) -> set:
    """The set of gmail ids that already have an OPEN FileMates reminder. This is the STABLE
    second dedup axis: unlike task_key(topic) (LLM-supplied, drifts between runs), the gmail id
    is deterministic, so it catches a re-processed same-mail task whose topic was reworded.
    Only OPEN reminders count — a fully-completed prior mail must not silently swallow a new task
    (that stays governed by the task-key + repeat-of-done rule)."""
    ids = set()
    for rem in reminders:
        if rem.completed:
            continue
        # parse_token returns None for a degenerate body with 0 OR ≥2 well-formed [gmail:]
        # tokens. Such a reminder (the tool never writes one — the id is the single mandatory
        # last line) is also not is_filemates_reminder and contributes no open_key, so the
        # whole tool treats it consistently as "no single identity". We deliberately do NOT
        # count it as an open id (favouring no-false-skip over catching a degenerate body).
        gid = parse_token(rem.body)                 # exactly-one well-formed [gmail:<id>] or None
        if gid:
            ids.add(gid)
    return ids


def plan_creations(candidates: list, open_keys: set, completed_keys: set,
                   open_gmail_ids: set = frozenset()) -> dict:
    """AC-R2..R5, R7. Pure: for each TaskCandidate (one per task), task_key → decide_create:
      'create' → build ReminderSpec(title=build_title, notes=build_notes, due=decide_due_date)
      'ask-*'  → recorded as an ask (never silently create).

    SECOND DEDUP AXIS (gmail-id): if task-key dedup says 'create' but the candidate's mail
    already has an OPEN reminder (open_gmail_ids, the frozen PRE-RUN snapshot), skip it silently
    — this is a re-processed same mail whose LLM topic merely drifted (real bug 2026-06-11), never
    a genuine new task worth a duplicate. open_gmail_ids is the live list only and is NEVER
    extended within the loop, so AC-R7 (several tasks for one mail in ONE run) is preserved: those
    candidates share a gmail id that is NOT in the pre-run snapshot, so they all create.
    Returns {'create': [ReminderSpec, …], 'asks': [...]}.  No I/O."""
    plan: dict = {"create": [], "asks": []}
    open_set = set(open_keys)                       # local copy so newly-planned keys dedup too
    for cand in candidates:
        key = task_key(cand.topic)
        if not key:                                 # S2: empty/blank topic → NO task identity.
            # A keyless candidate has nothing to dedup on; collapsing it on the empty key would
            # silently merge unrelated tasks (lost-task bug). Surface every one as an ask so
            # none is lost, and a real open task is never collapsed against a blank one.
            plan["asks"].append({"topic": cand.topic, "reason": "no-task-identity"})
            continue
        decision = decide_create(key, open_set, completed_keys)
        if decision == "create":
            if cand.gmail_id in open_gmail_ids:     # same mail already open under another topic
                continue                            # → silent skip (no duplicate); NOT an ask
            spec = ReminderSpec(
                title=build_title(cand.who, cand.what, cand.why, cand.grey_area),
                notes=build_notes(cand.recap, cand.mail_date, cand.gmail_id,
                                  task_key=key, filed_path=cand.filed_path),
                due_date=decide_due_date(cand.explicit_deadline),
            )
            plan["create"].append(spec)
            open_set.add(key)                       # a second candidate of the same task → skip-dup
        elif decision == "ask-repeat-of-done":      # repeat-of-done → ASK (never silent re-create)
            plan["asks"].append({"topic": cand.topic, "reason": decision})
        # decision == 'skip-open-dup' → silently do nothing (no duplicate, not an ask)
    return plan


def apply_creations(runner: OsascriptRunner, list_name: str, specs: list) -> None:
    """AC-R9. ensure_list, then runner.create_reminder for each ReminderSpec."""
    runner.ensure_list(list_name)
    for spec in specs:
        runner.create_reminder(list_name, spec.title, spec.notes, spec.due_date)


# ─────────────────────────────────────────────────────────────────────────────
# CLI (thin; the real seams are wired here — exercised by the human smoke, not CI)
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_LIST = "Email-Tasks"


def _candidate_from_json(obj: dict) -> TaskCandidate:
    """Build a TaskCandidate from one JSON object (the LLM supplies the content). Dates are
    ISO `YYYY-MM-DD`; missing optional fields degrade to None (never invented)."""
    def _as_date(value):
        return date.fromisoformat(value) if value else None

    return TaskCandidate(
        who=obj.get("who"), what=obj.get("what"), why=obj.get("why"),
        recap=obj.get("recap", ""),
        mail_date=date.fromisoformat(obj["mail_date"]),
        gmail_id=obj["gmail_id"],
        topic=obj.get("topic", ""),
        explicit_deadline=_as_date(obj.get("explicit_deadline")),
        filed_path=obj.get("filed_path"),
        grey_area=bool(obj.get("grey_area", False)),
    )


def _keys_from_reminders(reminders: list):
    """Split a reminder list into (open_keys, completed_keys) — the dedup inputs.

    B1: each live key is read back from the persisted `[task:<key>]` anchor via
    parse_task_anchor(body), NOT derived from the title — so the read-back key shares the
    create-side namespace (task_key(topic)) and cross-run task-level dedup actually matches.
    Reminders without a task anchor (manual, or pre-B1 untagged) carry no task identity → they
    contribute no key (never silently merged onto another task)."""
    open_keys, completed_keys = set(), set()
    for rem in reminders:
        if not is_filemates_reminder(rem.body):
            continue
        key = parse_task_anchor(rem.body)
        if not key:                                 # no persisted task identity → skip (not merged)
            continue
        (completed_keys if rem.completed else open_keys).add(key)
    return open_keys, completed_keys


def cmd_create(args, runner: OsascriptRunner) -> int:
    """Read TaskCandidates (JSON list inline via --json, from an --in file, or on stdin),
    dedup against the live list, plan and create. Prints the plan; asks are surfaced
    (never silently created)."""
    if getattr(args, "inline_json", None) is not None:
        # A malformed inline payload is a USAGE error (bad argv, typically an LLM quoting
        # slip), and it provably happens before any action — so report it cleanly with the
        # usage exit code 2 (same class as an argparse rejection) instead of a traceback.
        try:
            parsed = json.loads(args.inline_json)
        except json.JSONDecodeError as e:
            print(f"create: invalid --json payload (nothing was created): {e}", file=sys.stderr)
            return 2
        if not isinstance(parsed, list):
            print("create: invalid --json payload (nothing was created): "
                  "expected a JSON LIST of candidate objects", file=sys.stderr)
            return 2
    else:
        raw = Path(args.infile).read_text(encoding="utf-8") if args.infile else sys.stdin.read()
        parsed = json.loads(raw)
    # The list is well-formed, but an ELEMENT may still be malformed (a non-object, a missing
    # gmail_id, an unparseable mail_date — the same LLM-quoting-slip class). Build candidates
    # defensively so this is a clean usage error (exit 2, nothing created) instead of a
    # traceback — honouring the "nothing was created" promise across the whole --json surface.
    try:
        candidates = [_candidate_from_json(o) for o in parsed]
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        print(f"create: invalid candidate in payload (nothing was created): {e}", file=sys.stderr)
        return 2
    live = runner.list_reminders(args.list)
    open_keys, completed_keys = _keys_from_reminders(live)
    open_gmail_ids = open_gmail_ids_from_reminders(live)
    plan = plan_creations(candidates, open_keys, completed_keys, open_gmail_ids)
    if not args.dry_run:
        apply_creations(runner, args.list, plan["create"])
    print(f"create: {len(plan['create'])}  ask: {len(plan['asks'])}"
          + ("  (dry-run)" if args.dry_run else ""))
    for ask in plan["asks"]:
        print(f"  ASK ({ask['reason']}): {ask['topic']}")
    return 0


def cmd_react(args, runner: OsascriptRunner, resolver: MailResolver) -> int:
    """React to user check-offs: sort exactly the mails whose every sibling to-do is done."""
    label_map = json.loads(Path(args.label_map).read_text(encoding="utf-8")) if args.label_map else {}
    report = react_to_checkoffs(runner, resolver, args.list, label_map)
    # Unattended-run policy: print, for this mode, whether a sort/delete may auto-execute or queues.
    mode = getattr(args, "mode", "attended")
    print(f"mode: {mode}  (sort -> {decide_unattended('sort', mode)}, "
          f"delete -> {decide_unattended('delete', mode)})")
    print(f"ready-to-sort: {len(report['sorted'])}  asks: {len(report['asks'])}")
    # NOTE: this CLI uses a bare MailResolver (resolve→0), so it never actually resolves or
    # archives mail — the Gmail-MCP resolve + single-id sort is driven by Claude per SKILL.md
    # Phase 2 (following the helper's exactly-one rule). 'asks' here are structural, not data.
    for gid in report["asks"]:
        print(f"  ASK: [gmail:{gid}] — Claude resolves via Gmail MCP; sort only if exactly 1 mail + label mapped.")
    return 0


def cmd_check_catchup(args) -> int:
    """Catch-up gate for the scheduled run. Reads schedule_times + the last-success state and
    decides whether a scheduled slot is overdue & unfulfilled. Prints DUE <n> / NOOP. Exit
    10 = DUE (a real crash exits 1/2, never misread as due); 0 = NOOP. ZERO Gmail/disk
    mutation — the cheap pre-flight before the full unattended flow."""
    cfg_path = Path(args.config) if args.config else DEFAULT_CONFIG
    state_path = args.state or STATE_FILE
    enabled, times_raw = read_schedule_config(cfg_path)
    if not enabled:
        print("NOOP (schedule_enabled != yes)")
        return 0
    times = parse_schedule_times(times_raw)
    if not times:
        print("NOOP (no schedule_times configured)")
        return 0
    if getattr(args, "now", None):                      # test-only override; prod uses real now
        now = datetime.fromisoformat(args.now)
        if now.tzinfo is None:
            now = now.astimezone()
    else:
        now = datetime.now().astimezone()
    due = due_slots(now, times, read_last_run(state_path))
    if due:
        print(f"DUE {len(due)}")
        return 10
    print("NOOP")
    return 0


def cmd_record_run(args) -> int:
    """Record a SUCCESSFUL scheduled run (atomically). The SKILL calls this ONLY after the
    full flow finished cleanly — a crashed/degraded run must leave the timestamp so the next
    tick retries."""
    if args.at:
        ts = datetime.fromisoformat(args.at)
        if ts.tzinfo is None:
            ts = ts.astimezone()
    else:
        ts = datetime.now().astimezone()
    write_last_run(args.state or STATE_FILE, ts)
    print(f"recorded last_success={ts.isoformat()}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FileMates reminder-helper — Apple Reminders ↔ mail.",
                                 allow_abbrev=False)   # destructive-adjacent CLI: full flags only
    ap.add_argument("--version", action="version", version=f"FileMates reminder-helper {__version__}")
    ap.add_argument("--list", default=DEFAULT_LIST, help=f"dedicated list (default: {DEFAULT_LIST})")
    sub = ap.add_subparsers(dest="cmd", required=True)
    # allow_abbrev is NOT inherited by subparsers (each add_parser builds a fresh
    # ArgumentParser) — without repeating it, `--js`/`--dry` would silently work and the
    # "full flags only" promise above would be false for every subcommand flag.

    p_create = sub.add_parser("create", allow_abbrev=False,
                              help="plan + create reminders from TaskCandidates (JSON)")
    # --in and --json are argparse-level exclusive: rejecting both BEFORE any action keeps
    # this a pure usage error (exit 2, nothing touched).
    g_create_src = p_create.add_mutually_exclusive_group()
    g_create_src.add_argument("--in", dest="infile", help="JSON file of candidates (default: stdin)")
    g_create_src.add_argument("--json", dest="inline_json", metavar="JSON",
                              help="candidates as an inline JSON list — lets a single plain "
                                   "command carry its payload (no temp file; useful where "
                                   "unattended runs may not write files)")
    p_create.add_argument("--dry-run", action="store_true", help="plan only, create nothing")

    p_react = sub.add_parser("react", allow_abbrev=False,
                             help="react to user check-offs (sort completed mails)")
    p_react.add_argument("--label-map", help="JSON file mapping gmail_id -> label")
    p_react.add_argument("--mode", default="attended",
                         choices=["attended", "auto-sort", "collect", "report-only"],
                         help="unattended-run policy (default: attended = human present)")

    p_check = sub.add_parser("check-catchup", allow_abbrev=False,
                             help="catch-up gate: exit 10 if a scheduled slot is due, else 0 (NOOP)")
    p_check.add_argument("--config", help="config.local.md path (default: the skill config)")
    p_check.add_argument("--state", help="state file path (default: .filemates-last-run.local.json)")
    p_check.add_argument("--now", help="ISO timestamp override for 'now' (testing only)")

    p_record = sub.add_parser("record-run", allow_abbrev=False,
                              help="record a SUCCESSFUL scheduled run (call only after the flow succeeds)")
    p_record.add_argument("--at", help="ISO timestamp override (default: now)")
    p_record.add_argument("--state", help="state file path (default: .filemates-last-run.local.json)")

    args = ap.parse_args(argv)
    if args.cmd == "check-catchup":
        return cmd_check_catchup(args)
    if args.cmd == "record-run":
        return cmd_record_run(args)
    if args.cmd == "create":
        return cmd_create(args, OsascriptRunner())
    if args.cmd == "react":
        return cmd_react(args, OsascriptRunner(), MailResolver())
    ap.error(f"unknown command {args.cmd!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
