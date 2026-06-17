---
name: filemates
description: "FileMates — pulls receipts, invoices and documents out of a Gmail inbox and files them on disk (renamed to the user's scheme, verified), then triages the rest. Built for the self-employed/freelancers whose real pain is documents. Downloads attachments, renames them to the user's scheme, and files them into configured folders (verified on disk); creates reminders for actionable mail; labels/archives only safely-finished threads; proposes junk/finished receipts for the trash while a protected-sender list prevents deletion of important senders (tax advisor, bank, clients). Reads config.local.md for the account, label map, folder map, naming scheme, protected senders and delete rules. Use when the user says 'check email', 'triage my inbox', 'clean up my inbox', 'scan mail', 'file my receipts', or 'delete junk mail'. macOS-first — task backend is Apple Reminders via AppleScript."
---

# FileMates

Pull the user's receipts, invoices and documents out of their Gmail inbox and file them on
disk (renamed + verified), then triage the rest: create reminders for mail that needs an action, download and file relevant attachments, label/archive only what is safely done, and propose clear junk for the trash — never losing a real task and never deleting protected mail.

> **Platform:** macOS-first (Reminders via AppleScript). Attachment download uses the IMAP downloader `tools/fetch-attachments.py` (OAuth or app password — set up once, see README); Claude-in-Chrome is only an optional fallback. The download itself is provider-agnostic; the Reminders/AppleScript part is macOS-only.

> ## ⚠️ Core invariant — "delete" ALWAYS means Trash, never permanent
> Whenever FileMates "deletes" **anything** — an email, a PDF, a file, a folder — it means
> **move to the recoverable Trash/Papierkorb**, never an unrecoverable wipe. This holds for every
> path and every mode (interactive or scheduled):
> - **Email** → move to the provider's Trash (Gmail `["TRASH"]` / the downloader's recoverable
>   Trash folder). FileMates **never** issues a permanent `EXPUNGE`; the `--force-expunge` flag is
>   off by default and is **never set by any FileMates flow**.
> - **Files / folders** → macOS Trash (e.g. Finder "move to Trash" / `trash`), **never** `rm`/`rm -rf`.
>
> **Emptying the Trash / "delete forever" is solely the user's own manual responsibility** — exactly
> like the Mac itself (a deleted folder sits in the Trash until *you* empty it). FileMates never does
> that step. The recovery window after that belongs to the provider/OS (Gmail ≈ 30 days; others
> vary), not to FileMates. This single rule removes any ambiguity about "weg-weg" vs "Papierkorb".

## Step 0 — First-run setup sprint (if there is no config yet)

If `config.local.md` does **not** exist, do **not** start triaging. Run a short guided
**setup sprint** with the user first — destructive email rules must be the user's, not
guessed:

1. **Connect & confirm the Gmail MCP.** Confirm which connector is set up and its tool
   prefix; do a trial `search_threads` for `in:inbox` to prove access.
2. **Build the protected / never-delete list together.** Ask who must never be touched
   (tax advisor, bank, lawyer, authorities, key clients) and which categories
   (contracts, invoices with retention duty, tax). Write them to
   `reference/delete-rules.local.md`.
3. **Build the delete list & junk rules.** Ask which newsletters/senders are clearly junk,
   and the nuanced rules (e.g. "calendar invites whose date has passed = delete",
   "shipping confirmations after delivery = delete", "invoices = file then delete the
   mail"). Capture exact senders as the user confirms them. Senders the user marks
   **delete-after-filing** are trashed automatically by the downloader once their
   attachment is verified-filed — but **only ever reversibly** (the downloader probes
   the server first and moves the mail to its recoverable Trash; permanent deletion is
   refused unless you pass `--force-expunge`). Disable trashing entirely with
   `--no-trash`. Everything else stays propose-first.
   **Inline-attachment safeguard:** documents sent as `Content-Disposition: inline`
   (some Apple Mail / forwarded mail) are *not* downloaded — they appear under *Skipped*.
   The downloader will **never auto-trash** a mail that still holds an unfiled inline
   document (even if a sibling attachment filed), so the only copy is never lost. When you
   see such a mail, leave it in the inbox and handle the inline document by hand.
   **Detect the safe delete mode once:** as soon as IMAP auth works, run
   `python3 tools/fetch-attachments.py --detect-delete` and record the printed
   `delete_mode:` / `trash_folder:` lines in `config.local.md`. The provider doesn't change
   between runs, so later runs read these instead of re-probing the server each time.
4. **Define the folder map + file-naming scheme.** Folder map = which document type files
   to which folder. Naming: **propose the default** `<prefix>_<type>_<sender>_<date>`, then
   **offer to adjust it** — the user sets `naming_scheme` in config (placeholders
   `<prefix> <type> <sender> <date> <original>`; `<original>` keeps the sender's own
   filename, handy when one mail carries several documents). Confirm their choice.
5. **Choose the task backend** (Apple Reminders by default; see README). **Set up the task list +
   recommend the widget** as part of onboarding:
   - Use a **dedicated list** `Email-Tasks` (name configurable via `reminders_list`). **Create it
     if it doesn't exist**; FileMates only ever touches this list — the user's other lists and
     their own manual reminders stay untouched. If the user already uses Reminders, this is just a
     separate second list.
   - **Recommend adding a desktop widget** for that list and walk the user through it
     step-by-step (this is how they'll check tasks off — the completion signal). FileMates
     **cannot** place the widget itself (macOS only lets the user add widgets); it works without
     one too, but the widget is the comfortable view. Steps *(macOS Sonoma/Sequoia; wording may
     differ slightly by version)*:
     1. Right-click an empty spot on the **Desktop** → **"Edit Widgets"**. *(Or: click the
        date/time at the top-right menu bar → bottom → "Edit Widgets" for Notification Center.)*
     2. In the widget gallery, pick **"Reminders"**.
     3. Choose a size and drag it onto the Desktop (or Notification Center).
     4. **Right-click the widget → "Edit Widget" → set List = "Email-Tasks"** (so it shows only
        FileMates tasks).
     5. Done — ticking a task in the widget = done; FileMates reacts to that check-off on its next run.
   - **Offer automatic runs (optional).** Ask if they want FileMates to run on a schedule, and let
     them **personalize the times** (e.g. `09:00, 18:00`) and the unattended-run **mode**
     (`report-only` / `collect` / `auto-sort` — see "Scheduled / unattended runs" + `schedule_*`
     in config). Default is **off** — never set up a schedule without the user opting in. When they
     opt in, write `schedule_enabled: yes` + their `schedule_times`/`schedule_mode`, then register
     a **catch-up-aware schedule** with the host scheduler: tick more often than the real slots
     (e.g. 4×/day) and have each tick run the `check-catchup` gate first, so a slot the machine
     slept through is still caught up (see "Scheduled / unattended runs"). Each due run invokes
     FileMates against `schedule_account` in the chosen mode. Confirm the times + mode back to them.
6. **Dry-run on a small sample (~20 mails) and review together.** Classify them, show the
   user the proposed action per mail, and correct the rules until the classification
   matches their intent. **Only after the user signs off** on the rules do you proceed to
   normal operation. Treat unknown senders as "ask the user", never as auto-delete.

### Label structure — discover first, then adopt or create
Labels are part of the setup sprint (do this around step 2-4). **Always run `list_labels`
first** and branch on what the account already has — never impose a taxonomy blindly:

- **The user already has a label system (most do — e.g. nested `Area/Sub` labels).**
  *Adopt it as-is.* Draft the sender/topic → label map from their **existing** labels,
  always choosing the most specific **sub-label**, show it for confirmation, and let them
  extend it. **Never create a duplicate** of a label that already exists, and don't layer
  your own structure on top of theirs.
- **The account has no / only a couple of labels.** Propose a small **starter taxonomy** of
  nested `/` sub-labels (e.g. `Admin/{Tax,Payroll,Phone,Travel,IT,General}`,
  `Clients/<name>`, `Car/Insurance`, `Private/<topic>`), tailored to what you saw in the
  sample. **Only after the user confirms**, create them with `create_label` (create a parent
  before its children if the connector requires it). Keep it small — a few clear buckets
  beat a sprawling tree.
- Write the agreed mapping to `config.local.md` under **Label map**, and record whether the
  user allows **auto-creating missing sub-labels** later (else: always ask first).
- **At runtime (Phase 5):** if a mail's mapped label does not exist yet, do **not** fall
  back to the parent or a wrong label — create the sub-label if the user allowed it here,
  otherwise surface it and ask. Mislabelling is as bad as losing the mail.
- **Connector capability note:** Gmail MCP connectors differ. Many support `create_label`
  (incl. nested `Area/Sub` via `/`) and message labelling but **deny `delete_label`**
  ("caller does not have permission"). So: *creating* a taxonomy works headlessly, but
  *removing* a retired label is usually a manual step the user does in Gmail (or via the
  browser). Don't promise label deletion via the connector — probe once if unsure, and tell
  the user to delete a label by hand if it's blocked.

> **⚠️ Inspect a label COMPLETELY before claiming what's in it (or emptying/retiring it).**
> Never say a label is "empty / done / only sender X" off a partial search.
> 1. **Search by label NAME, not ID** — some connectors return `{}` for `label:<ID>`. Use
>    `search_threads` with `label:"Area/Sub" in:anywhere` and `includeTrash: true`.
> 2. **An empty/tiny result is a suspicion, not a fact** — re-query (name vs ID) before trusting it.
> 3. **Paginate fully** (`pageSize: 50` + `nextPageToken`); never generalize from one sender —
>    check the sender variety (a "charging receipts" label may hold several providers, not one).
> 4. **Only after the full list** do you judge / trash / retire — and verify each kept document
>    is on disk first, so an un-filed receipt is never lost.

This sprint is the difference between a tool the user trusts with delete rights and one
that quietly loses their mail. Re-run it any time by deleting `config.local.md`.

## Step 0.1 — Load configuration

Read `config.local.md` in this skill's folder. If it does not exist, run Step 0 first. The config defines:
- `account_email` — the Gmail address being triaged (one account only)
- **Label map** — sender/topic → Gmail label
- **Folder map** — attachment type → local folder
- **Protected senders / categories** — never deleted (your real list is in `reference/delete-rules.local.md`; `delete-rules.example.md` is only the template)
- **Reminders list name** — the Apple Reminders list for tasks (e.g. `Email-Tasks`)

Never hard-code personal data in this file. All of it lives in `config.local.md`, which is git-ignored.

## Core rule (the safety net)

**A mail only leaves the inbox if one of these is clearly true:**
1. **Answered** — the last message in the thread is from `account_email`, or
2. **Clear junk** — newsletter, ad, pure marketing/automated notice with no document and no action.

In every other case the mail **stays in the inbox and gets a reminder**. When in doubt: leave it and create a reminder. Better two mails too many in the inbox than one lost task.

**Attachment invariant:** if a mail carries a document worth keeping, it may only be archived/cleaned up **after the file is verified at its destination** (Phase 4). If filing fails → leave the mail, create a "file attachment manually" reminder.

## Tool rules
- Read mail → Gmail MCP (`search_threads` / `get_thread`). Use the fully-qualified MCP tool names of the connector configured in `config.local.md`.
- Reminders → AppleScript only, via the macOS-control MCP (`osascript`). Never open/click the Reminders app.
  - **Generated-AppleScript guardrails (verified the hard way):** do NOT use AppleScript reserved/abbreviation words as variable names — `at`, `st`, `in`, `id`, `date`, `name` all break the parser; use safe names (`theStatus`, `theName`, …). AppleScript has **no inline `if … then x else y` expression** — use explicit `if/else` blocks. (Common German text incl. umlauts ä/ö/ü works fine via osascript — the earlier breakages were reserved words, not non-ASCII.)
- Download attachments → Claude-in-Chrome. Move/rename/verify files → shell (`mv`, `ls`).
- Labels / archive / trash → Gmail MCP (`label_message` / `unlabel_message`; archive = remove `INBOX`; trash = add `TRASH`).
- **The helper commands are synchronous — never background them, never poll for them.** Each
  `reminder-helper.py` / `fetch-attachments.py` call runs in the foreground and returns only when
  it is finished; the next step begins after you read its stdout. Do **not** launch them in the
  background and do **not** write a wait/poll loop (`while`, `kill -0`, `pgrep`, `sleep`, `wait`)
  to "wait for the tool to finish" — there is nothing to wait for, and such loops both are an
  illusion of work *and* (with their `$(…)`/`;`/redirects) stall an unattended run on a permission
  prompt. If you feel the urge to wait for one of these tools, that urge is the bug.

---

> ## Reminder tooling — use the tested helper, not hand-written AppleScript
> All reminder reads, dedup, and creation go through **`tools/reminder-helper.py`** — a tested
> Python helper (a unit/seam/security/schedule test suite; the load-bearing invariants — exactly-one-sort,
> no-duplicate, open-wins dedup, catch-up gating — have dedicated kill-the-mutant tests, manually mutation-checked;
> no automated mutation oracle has been run). It **owns** the AppleScript generation (the reserved-word/inline-if landmines live
> in one tested place) and enforces the load-bearing guarantees mechanically: task-level dedup
> across open+completed+overdue, the `Wer - Was - Warum` title, the ordered notes with the
> `[task:<key>]` + mandatory-last `[gmail:<id>]` anchors, never an invented deadline, and
> "sort exactly one mail / else ask". **Do not hand-write `make new reminder` AppleScript** — the
> snippets below are only *reference* for what the helper emits. The mail-resolve + sort step
> (Gmail MCP) is the one part Claude still drives (the helper can't call the Gmail connector);
> Claude must follow the helper's exactly-one rule there. *(That live sort + check-off round-trip
> is the human-in-loop smoke; everything else is CI-tested.)*

## Scheduled / unattended runs (optional, user-configurable)
FileMates is **not a background daemon** — it acts only when invoked. A user may opt into
automatic runs via the `schedule_*` config (times + mode are **personalized per user**; default
off). Setup (Step 0) reads the user's desired times and registers them with the host scheduler;
each run invokes FileMates against `schedule_account`.

**Catch-up gate (so a slept-through slot is not lost).** Host schedulers only fire while the app
is open and may skip a slot if the machine was asleep at that minute. So the scheduled task is
registered to tick **more often than the real slots** (e.g. 4×/day) and each tick runs a cheap
gate **first, before any Gmail access**:
`python3 tools/reminder-helper.py check-catchup` → exit `10`/`DUE` = a real slot (`schedule_times`)
is overdue & unfulfilled → run the full flow; exit `0`/`NOOP` = nothing due → **stop immediately**
(no inbox scan). After a **successful** full run, and only then, call
`python3 tools/reminder-helper.py record-run` to advance the last-success timestamp
(git-ignored `.filemates-last-run.local.json`). A crashed/degraded run must **not** call
`record-run`, so the next tick retries. A missed slot is caught up within a fixed 12h staleness
window (a slot crossed longer ago is not run pre-dawn). This is the tested `due_slots(...)` pure
function — the gate decides only *whether* to run, never *what* may run.

**Pre-allow the run's actions, or it will hang (learned in production).** If your host gates
tool calls behind permission prompts, an unattended run **stalls silently** on the first
non-pre-approved action — and a stalled run also queues up the ticks behind it. Prompt-clicked
approvals are often only session-scoped, so the same prompt returns on every run; put a
standing allow-list in your host's settings instead. The complete surface a scheduled FileMates
run needs (observed from real run transcripts — read-only except the two tools):

- **Run the two tools** (absolute paths): `python3 <repo>/tools/fetch-attachments.py *`,
  `python3 <repo>/tools/reminder-helper.py *`
- **Read**: the FileMates repo (the run reads `SKILL.md`, `config.local.md`,
  `delete-rules.local.md` every time) and your filing folders
- **No file writes needed**: pass reminder candidates inline via `create --json '<list>'`
  (see Phase 3) instead of a temp file — file-write permission rules proved unreliable
  across hosts in production (e.g. macOS resolves `/tmp` to `/private/tmp` before matching,
  so a `/tmp/...` allow rule silently never fires), and a run that *needs no Write tool at
  all* cannot hang on one.
- **Shell**: `ls *` and `grep *` (read-only verification/lookup)
- **Mail connector**: search/get/list-labels/label/unlabel — the reversible set only
- **Deny** (defense-in-depth): anything matching `*--force*` (blocks `--force-expunge`)

Known residual prompt: moving a **file** to the OS Trash (e.g. via Finder/`osascript`) is not
on this list — deliberate, since it is rare; expect one prompt when it happens.

An unattended run has **no human present to confirm**, so it obeys the unattended-run policy
(`decide_unattended(action, mode)` in the helper — the tested source of truth):

| `schedule_mode` | create reminders | sort (label+archive, reversible) | trash — **rule-covered** (sender/category on the user's delete-list) | delete — **ad-hoc** (suspected junk w/o a rule, or permanent) |
|---|---|---|---|---|
| `report-only` | queue (report only) | queue | queue | queue |
| `collect` | **execute** (additive, deduped) | queue for confirmation | queue | queue |
| `auto-sort` | **execute** | **execute** iff check-off resolves to exactly 1 mail + mapped label | **execute** (reversible Trash; protection always wins) | **queue** (never unattended) |

This is `decide_unattended(action, mode)` in the helper (tested source of truth: action ∈
create / sort / trash-rule / delete). **Rule-covered trash auto-runs — that's the whole point of
the user's delete-rules** (decide once, then it runs without re-asking; reversible 30-day Trash;
protected senders excepted). A deletion **without** a standing rule is `delete` → it is queued and
**proposed**; if the user then says "always delete from this sender", it is **added as a rule**, so
next time it is `trash-rule` and runs automatically (the learn-the-rule loop).

Hard rules for any unattended run, regardless of mode:
- **Never permanently delete, and never ad-hoc-delete unattended** — only an explicit standing
  delete-rule authorizes auto-trash (and only reversibly to Trash). Everything else is queued.
- **Protection always wins** — a protected sender/category is never trashed on any path or mode.
- **Auto-sort only on the exactly-one + mapped-label path** (AC-R1). 0 / ≥2 / no-label → queue, never act.
- **Never invent** a reminder when uncertain (repeat-of-done, no task identity, unclear state) → queue it as "to clarify", don't create.
- Always end with a **run report**: created, auto-sorted, and the queue of items awaiting the user's confirmation. Nothing invisible.
- If the Gmail MCP isn't available in the scheduled context, the run **degrades safely**: it can still read Reminders + report, but any mail action that needs Gmail is queued, not failed-open.

## Phase 1 — Read existing reminders (open AND completed)
The helper reads the dedicated list itself (open **and** completed) and computes the dedup inputs
from the persisted `[task:<key>]` anchors — you do not hand-write the read. The dedup guard is
**task-level across open + completed + overdue**: a task that already has a reminder in **any**
state must never get a second one; a previously-**completed** task re-appearing → the helper
**asks**, never silently re-creates (this is the fix for the "same task created ~6×" bug).
Completed reminders carrying a `[gmail:<id>]` are the user's check-offs → Phase 2.

**Second dedup axis — the stable gmail-id.** Task-level dedup keys on `task_key(topic)`, and the
`topic` is model-supplied: across two runs the same mail can be phrased slightly differently
(e.g. one run adds a `06-2026` suffix), producing two keys and — historically — a duplicate. So
the helper also dedups on the **mail id**: if a candidate's `gmail_id` already has an **open**
reminder, it is **skipped** even when the task-key missed (the mail is already represented).

**Exact guarantee + its deliberate edge (honest):** AC-R7 — several genuine to-dos for one mail
created together — holds for the **first run that reminds a mail**, i.e. while that mail has **no**
open reminder yet (the guard consults only the frozen pre-run list, so same-run siblings of a
not-yet-open mail all create). **Once a mail has an open reminder, every further candidate for that
same mail is skipped** — even a genuinely new, distinct task, even when delivered alongside a
re-detected one in a later run. This is a deliberate trade-off, not an oversight: a drifted-topic
duplicate and a real new task are indistinguishable from the *(already-open mail, new task-key)*
signal alone, and the chosen rule is **never duplicate** over **ask** (a rare genuinely-new task on
an already-open mail must instead be added by the user). The behaviour is pinned by a test.

## Phase 2 — React to reminders the USER checked off
Completion is **user-driven**: the user marks a task done by **checking off** its reminder in the Email-Tasks list. **The check-off is the ONLY "done" signal.** FileMates must **never** infer completion from mail activity (it must NOT assume "the mail was answered") — the user very often finishes a task **off-channel** (a bank transfer, WhatsApp, a phone call, in person; e.g. paying a tax bill) with **no email reply at all**. No check-off → not done, regardless of what happened in the mailbox.

The helper decides **which** mails are ready (completed reminder, real `[gmail:<id>]` token, and —
for multi-to-do mails — **all** siblings done) and applies the count rule (resolve → exactly **1**
mail **and** a label mapped ⇒ sort; **0, ≥2, or no label ⇒ ask**). You execute the resolve + sort
via Gmail MCP, strictly one explicit message id at a time, following that rule:

For each FileMates-created reminder (carries a `[gmail:<id>]` tag) that the user has marked **completed**:
1. **Resolve the EXACTLY ONE linked mail** via the reminder's `[gmail:<id>]`, and sort **only that one mail** away into its category (most specific mapped label + archive, remove `INBOX`). **Never bulk-archive / never touch any mail that wasn't the one linked to a checked-off reminder** — sorting away mails the user didn't check off is a bug (the user then has to dig the mail back out).
   **Multi-to-do mails:** if the same `[gmail:<id>]` is linked by **several** to-dos (one mail → many tasks), sort/delete the mail **only once ALL of them are checked off** — never while a sibling task is still open.
2. **If the `[gmail:<id>]` does not resolve to a real mail, or no label/category is mapped for it → ask the user**; never guess a label and never act on a different mail.
3. **Never auto-delete.** Deleting a mail happens **only after asking the user**, and only for clear call-to-action mails. There is **no** time-based auto-delete of reminders or mail.

**NEVER modify or delete a reminder without a `[gmail:<id>]` tag** — those are the user's own manual reminders and are strictly off-limits (the list mixes both).

## Phase 3 — Scan + classify the inbox
Fetch `in:inbox`, read each thread (full content).

**Waiting list first — skip beats classification.** If the local config has a *Waiting list*
section (long-running cases where the user awaits an external outcome that won't arrive by
mail), check every mail against it **before** bucketing. A match → skip the mail entirely:
no attachment download (not even "again, to be safe" — re-fetching a long-pending mail is how
duplicates happen), no reminder, no archive/trash, no repeated queue question. Newly arrived
mails that match an open case join it silently. Report exactly **one** line per case:
`waiting: <case> (since <date>, N mails in inbox)`. Only the **user** closes a case (in chat);
then apply its *On close* action. Never edit the waiting list in an unattended run.

Put each remaining mail in exactly one bucket (first match wins):
- **A — Answered** → cleanup (Phase 5).
- **B — Clear junk** (newsletter/ad/marketing, no document, no action) → cleanup (Phase 5).
- **C — Actionable** (sign/return a document, pay an invoice, confirm an appointment, deliver info, answer a real question, implicit asks) → create reminder, **mail stays in inbox**; if it has an attachment, also Phase 4.
- **D — Grey area / unsure** (insurance, authorities, tax, invoices, contracts, "your documents", or simply not clearly A/B/C) → create a reminder (title prefixed "Review:"), **mail stays in inbox**; file any attachment (Phase 4) but keep the mail.

> Never archive C or D. Only A and B are cleaned up.

Create a reminder **only if its Gmail ID is absent from the Phase-1 read across ALL states** (open + completed + overdue) — hard dedup rule. `make new reminder` does **not** dedup by itself. An **overdue** reminder is NOT a reason to create a new one: leave the existing reminder as-is (its ID already exists) — re-creating a second to-do for an expired one is a bug.
**Title convention — `Wer - Was - Warum`** (crisp, scannable in the Reminders list):
`<Who / from whom> - <What they want> - <Why>`, e.g.
`Alex Beispiel - Büroumzug - Wegen Rückerstattung Umzugskosten`.
- Use the sender's **real name** if identifiable (else org / address).
- **Only include a part the mail actually supports — never invent a Who/What/Why** (AC-R2 spirit). If a part is genuinely absent, **drop it** (`Wer - Was`) rather than fabricate a reason.
- Keep it short. Grey-area (bucket D) keeps the **`Review:`** prefix → `Review: Wer - Was - Warum`.

**Body / notes** (Apple Reminders shows this only in the reminder's **detail/full view**, so it can carry more than the title). Write it as these lines, in order:
1. **Recap** — 1–3 sentences, a bit fuller than the title: what's going on / what's needed.
2. **`Mail vom <TT.MM.JJJJ>`** — the mail's own date ("von wann"), so the user has the timeframe.
3. **`gefilt: <path>`** — only if an attachment was filed (the verified on-disk path).
4. **`[gmail:<id>]`** — **mandatory, always the last line**. The load-bearing link for
   "check-off → sort exactly this mail" and for dedup. Never put it in the title; never omit it.

Same no-invent rule: only facts the mail actually supports (the date is the mail's real date).
Add a deadline **only if the mail explicitly names one** — **never invent one** (no stated date → no due date).

**How to create — hand the helper a JSON list of task candidates** (one object per task; AC-R7:
a mail with several actions → several objects, never one bundled mega-task). The helper dedups
against the live list, builds the title/notes/anchors, sets a due date only if you pass an explicit
one, and creates the survivors; repeat-of-done and identity-less tasks come back as **asks** for you
to raise with the user:

```bash
echo '[{"who":"Alex Beispiel","what":"Büroumzug","why":"Wegen Rückerstattung Umzugskosten",
  "topic":"Büroumzug Rückerstattung","recap":"Alex bittet um Beleg/Bestätigung zur Rückerstattung der Umzugskosten.",
  "mail_date":"2026-04-06","gmail_id":"aaa111","grey_area":false}]' \
  | python3 tools/reminder-helper.py --list "<REMINDERS_LIST>" create
# add "explicit_deadline":"2026-04-30" ONLY when the mail names a date; "filed_path" iff an attachment was filed;
# "grey_area":true for bucket D (→ "Review:" prefix). --dry-run plans without creating (it still
# reads the live list for the dedup check; a not-yet-existing list degrades to empty, no crash).
```

**Unattended runs: pass the candidates inline instead** — `create --json '<list>'` carries the
payload inside the one plain command, so the run needs **no temp file and no pipe** (both are
typical permission-prompt sources that stall an unattended session):

```bash
python3 tools/reminder-helper.py --list "<REMINDERS_LIST>" create --json '[{"who":"Alex Beispiel", … }]'
# wrap the JSON in single quotes; escape a literal ' inside as '\''. --json and --in are
# mutually exclusive, and a malformed/non-list --json payload is the same usage-error class:
# both exit 2 with a clear message BEFORE anything is touched (nothing read, nothing created),
# so an unattended run can treat them like any argparse slip — fix the command and retry.
```

Candidate fields: `who/what/why` (drop a part the mail doesn't support — the helper omits Nones,
never invents), `topic` (the **task identity** for dedup — same real task across mails must carry the
same topic), `recap`, `mail_date` (ISO), `gmail_id`, optional `explicit_deadline` (ISO), `filed_path`,
`grey_area`. The helper writes the notes as: recap · `Mail vom TT.MM.JJJJ` · `gefilt:` (iff filed) ·
`[task:<key>]` · `[gmail:<id>]` (last). It emits AppleScript like the reference below — you don't write it:

```applescript
make new reminder with properties {name:"Alex Beispiel - Büroumzug - Wegen Rückerstattung Umzugskosten", body:"Alex bittet um Beleg/Bestätigung … " & linefeed & "Mail vom 06.04.2026" & linefeed & "[task:büroumzug rückerstattung]" & linefeed & "[gmail:aaa111]"}
```

## Phase 4 — Download + VERIFIED filing of attachments
For keep-worthy attachments (PDF/DOCX/XLSX — not signatures/logos/ICS).

**Preferred: the IMAP downloader `tools/fetch-attachments.py`** (provider-agnostic, no
browser, deterministic). It downloads, renames, files into the folder map, and verifies.
Example: `MAIL_APP_PASSWORD=… python3 tools/fetch-attachments.py --from <sender> --since <date> --type invoices`.
It deletes **only** senders on the user's `delete-after-filing` list, **only** after the
attachment is verified on disk, and **only reversibly** (it probes the server and moves the
mail to its recoverable Trash — permanent `EXPUNGE` is refused without `--force-expunge`).
Protected senders are never deleted. Disable all trashing with `--no-trash`; everything not
on the delete-after list stays propose-first (Phase 5b). Use this whenever an
IMAP account (or a Gmail MCP with an attachment-download tool) is available. Auth is
automatic: app password (env `MAIL_APP_PASSWORD`) for personal accounts, or OAuth/XOAUTH2
for Google Workspace/business accounts where app passwords are blocked (one-time
`--auth-setup`; see README → "OAuth setup").

**📎 Multi-document mails — file one document at a time with `--attachment`.** One mail can
carry several *different* documents that belong in *different* folders (a tax advisor's mail with
an invoice + a tax-office protocol + a missing-receipts list is the classic case). A single
downloader call files **all** attachments into the **one** folder of its `--type`, under the
**one** naming scheme — so the documents collide on the same name (`_v1`/`_v2`) in the wrong
place. Instead: read the mail's attachment list first, then call the downloader **once per
document** with `--attachment "<part of the original filename>"` (case-insensitive substring) plus
that document's own `--type` and `--name-sender`/`--name-type`. A selective `--attachment` fetch
**never trashes the mail** (the other documents are still in it) — cleanup stays with Phase 5b.

**🗂 One naming scheme per folder — flag & learn when it doesn't fit.** The goal is that every
file inside a folder (and its sub-folders) follows **one consistent naming convention**. A file
keeps a raw/idiosyncratic name only when the configured scheme doesn't actually produce a clean
result (e.g. the source filename is a cryptic vendor invoice number, or a legacy file predates the
scheme). When you notice a folder with mixed/inconsistent names: **don't silently leave it** —
surface it to the user, propose a concrete scheme (read the document to extract the real
number/date if needed), agree it, rename **all** affected files (including legacy ones), and record
the agreed scheme so it applies next time. Consistency is the target; a scheme that "doesn't catch"
a file is a signal to refine the rule with the user, not to leave an outlier.

**❓ Unidentifiable / unassignable documents — exhaust identification FIRST, then a review folder + ask.** A document goes to a "needs-review" bucket **only as a last resort**, after every identification avenue has genuinely failed:
1. filename, 2. **full text** (OCR the scan first if there's no text layer — see below), 3. the source mail (sender, subject, date, body), 4. cross-reference (amounts/numbers/dates that match another known doc), 5. the folder it came from.
Only if it's *still* unidentifiable → move it to a clearly-named review folder (e.g. `_needs-review/` / `_Klaerfaelle/`) with a date-stamped name, and **ask the user** what it is (don't guess a category, and don't silently leave it mislabeled). Never send a document to review that a bit more effort (especially OCR) could have identified. Once the user clarifies, file it and, if it's a recurring type, learn the rule.

**📄 Scanned PDFs / images — OCR first, then classify by content.** Many documents arrive as
**scans or photos with no text layer** (you can't read or sort them by content). Do not file
them by filename guesswork or leave them unsorted:
- Turn on `ocr: auto` (config) so the downloader adds a searchable text layer right after
  filing (Apple Vision on macOS, ocrmypdf on Linux — see README → OCR). Then the file's
  **content** is available to decide its type/date/destination.
- To classify a scan you already have, OCR it first (`tools/ocr-folder.py <dir>` or
  `--ocr auto` on the run), then read the text and file it into the right folder like any other
  document. A scan is a first-class document — it must end up as readable + sorted as a digital PDF.
- **Rotated/sideways scans:** the OCR tool auto-detects orientation (tries 0/90/180/270 and keeps the best) so they come out upright + searchable. If an OCR text layer still looks like garbled letter-salad, that's almost always rotation/scan quality — a **visual read is rotation-tolerant**, so look at the rendered page before declaring it unreadable. Garbled OCR ≠ unidentifiable.
- Only genuinely unreadable scans (auto-rotation AND a visual read both fail) go to a misc/review folder.

**🔒 Encrypted / password-protected PDFs — quarantine, never open.** Some attachments are
password/encryption protected. You usually do **not** have the password, their date can't be
read, and — critically — **opening such a PDF can corrupt the assistant's own context/session.**
Rules (the downloader enforces the first two automatically):
- The downloader detects an encrypted PDF and files it into the `protected_pdf_folder`
  sub-folder of the target (default `_Passwortgeschuetzt`), keeping the original name.
- It does **not** trash that mail (it may be the only way to recover the password) and does
  **not** try to read a date out of it.
- **Any assistant: never `Read`/open a file inside the `protected_pdf_folder`, and treat that
  whole folder as ignored** when scanning/processing a directory. If you ever hit a PDF that
  errors on read, move it into that folder and move on — do not retry.
- For browser/manual filing: if a downloaded PDF turns out to be protected, move it into the
  `protected_pdf_folder` and note it; don't attempt to open it.

**Fallback only (if no IMAP / no attachment tool): Claude-in-Chrome.** Strict order:
1. Download via Claude-in-Chrome (open the mail, download the attachment).
2. Find the file (it may land in Downloads or on the Desktop):
   `find "$HOME/Downloads" "$HOME/Desktop" -maxdepth 1 -type f \( -iname "*.pdf" -o -iname "*.docx" -o -iname "*.xlsx" \) -mmin -5`
3. Determine target folder + name from the **folder map** in config. Naming scheme: `<prefix>_<type>_<sender>_<YYYY-MM-DD>.<ext>`.
4. Move + rename (move, don't copy): `mv "<source>" "<target_folder>/<new_name>"`
5. **VERIFY** the file is at the destination: `ls -la "<target_folder>/<new_name>"`
   - Success → note the path in the reminder/report; only now may Phase 5 run for bucket A.
   - Failure → **do not archive.** Create a "file attachment manually" reminder and flag it in the report.

## Phase 5 — Cleanup: label + archive (only A + B)
For each A/B mail: set the most specific label from the **label map** (a sub-label, never just the parent), then archive (`unlabel_message` with `["INBOX"]`). Resolve label IDs via `list_labels`. If the mapped sub-label does not exist yet, handle it per *Label structure* in Step 0 — create it if the user allowed auto-creation, otherwise surface it and ask. **Never mislabel to the parent or a wrong label as a fallback.**

## Phase 5b — Delete (trash) — PROPOSE, don't auto-delete
Read the user's own `reference/delete-rules.local.md` (fall back to `delete-rules.example.md` only if the local file is absent). **Trash = add `["TRASH"]`** (30-day recoverable; never permanent).

> **Two deletion paths, one rule — deletion is always reversible.** This phase (the agent,
> via Gmail MCP) is **propose-first**: you never trash without the user's OK. The **downloader**
> (`tools/fetch-attachments.py`) is the *one* exception, and it is not a contradiction: it
> auto-trashes **only** senders the user explicitly put on the `delete-after-filing` list (and
> signed off in the Step 0 dry-run), **only** after the attachment is verified on disk, and
> **only reversibly** (it probes the server and moves to the recoverable Trash; permanent
> `EXPUNGE` needs `--force-expunge`). Protected senders are never trashed on either path.

Per mail, in order (protection always wins):
1. **Protection gate first.** If the mail matches a protected sender/category → never delete. At most archive. Done.
2. **Delete-allowed check.** Only if not protected: clear junk / receipt-after-verified-filing / sender on the delete list.
3. **Collect, don't delete.** Matches go on a proposal list — not trashed yet.

**Mode** (field in the delete-rules file):
- `propose` (default): show the list (`sender — subject — reason`), wait for the user's OK, then trash the confirmed ones. If the user says "always delete from this sender", add it to the delete list.
- `auto`: trash junk + allowed receipts without asking; protection still applies; still list everything in the report.

> When in doubt, **don't** delete — archive. A wrongly deleted mail is worse than one too many in the archive.

## Phase 6 — Plain-text report
Summarize honestly: scanned, reminders created, reminders completed, left in inbox (with reason), attachments filed (with verified path), filing problems, archived, trash proposals / trashed, inbox count now. Nothing invisible.
