# FileMates — get every receipt & document out of your inbox and onto your disk

**Exhausted from cleaning up after your inbox — and it still takes forever to find anything?**

FileMates is a [Claude Code](https://claude.com/claude-code) skill (macOS) for the annoying
part of running a company: it doesn't just *read and sort* your Gmail — it **downloads your
receipts, invoices and contracts, names them to your convention, and files them into your
folder structure** (verified on disk), so your bookkeeping isn't a shoebox at year-end and you
never hunt for a receipt again.

Most email skills stop at triage and reply-drafting. FileMates is built for **freelancers
and small companies (UG/Einzelunternehmen)** whose real pain is *documents*: the attachment
download → rename → file → verify pipeline is the core, not an afterthought. It can even
**propose a naming scheme and label structure for you** — tailored to what's actually in your
inbox — the know-how you'd normally pay an assistant or bookkeeper for.

On top of that it does the usual triage safely: creates reminders for mail that needs an
action, labels/archives only what is finished, and proposes obvious junk for the trash —
with a **protected-sender list** so your tax advisor, bank or clients are never deleted.

Guiding principle: **never lose a real task, never delete protected mail, never archive a
document mail until the file is confirmed on disk, and never delete irreversibly.** Before
deleting anything, the downloader **probes the mail server** and only ever moves mail to its
recoverable Trash (Gmail: ~30-day recovery, per Google's Trash retention); if no recoverable Trash exists it **skips
deletion** rather than risk it — permanent removal happens only if you explicitly pass
`--force-expunge`. The triage skill proposes deletions and acts only after you confirm; the
downloader additionally auto-trashes senders **you** put on the delete-after-filing list, but
only once the attachment is verified on disk (turn it off with `--no-trash`).

> ℹ️ This started as a personal tool and is shared "as is" in case it's useful to someone.
> It is **not** a polished product — see *Limitations* before relying on it.

## Before you start — you MUST configure these

This skill does **nothing useful out of the box** — it is driven entirely by your own
config. Set up all of the following first (details under *Configure*):

1. **Mail access for the downloader (do this first)** — **OAuth** (recommended; required for
   Google Workspace/business accounts) or an app password. This is what makes downloading
   work at all. Full step-by-step under *Setup — mail access*.
2. **A Gmail MCP connector** — for the triage/labelling part; you set it up yourself, the
   skill contains no Google credentials. Tell the skill your connector's tool prefix in
   `config.local.md`.
3. **Your protected / never-delete list** — senders & categories that must never be trashed
   (tax advisor, bank, lawyer, authorities, clients). Fill this first.
4. **Your delete list** — what *may* go to the trash (which newsletters/senders, which
   receipt types after filing).
5. **Your folder structure** — where each document type gets filed (the folder map).
6. **Your file-naming scheme** — `naming_scheme` in config (default
   `<prefix>_<type>_<sender>_<YYYY-MM-DD>`; adjust freely, placeholders incl. `<original>`).
7. **Your task backend** — where to-dos go. **Apple Reminders (macOS)** is what's
   implemented (that's the default); see *Task backend* if you want to use something else.

If `config.local.md` is missing, the skill will ask you to create it from the template
before doing anything.

## Requirements

- **macOS only.** Reminders are read/written through Apple Reminders via AppleScript.
- **Mailbox access for the downloader** — **OAuth** (recommended; required for Workspace) or
  an app password. One-time setup, then headless. See *Setup — mail access* below.
- **Claude Code** with:
  - a **Gmail MCP connector** that you set up yourself (for the triage/labelling part; this
    skill ships no connector and contains no Google credentials — point the skill at yours
    in config),
  - a macOS-control MCP that can run `osascript` (for Reminders),
  - *(optional)* the **Claude-in-Chrome** browser tools — only as a fallback download path.
- *(optional)* **OCR** — make scanned PDFs searchable; only if you set `ocr: auto`/`yes`. On
  **macOS** this uses Apple's built-in **Vision** (no extra packages — `install.sh` just
  compiles a tiny Swift helper; needs Xcode Command Line Tools). On **Linux** it uses
  **`ocrmypdf` + `tesseract`** (`install.sh` installs via `apt`). See *OCR* below.

## Install

```bash
git clone https://github.com/CompassFW/FileMates.git
cd FileMates
./install.sh        # copies the skill + the downloader into ~/.claude/skills/filemates/
                    # and creates config.local.md + delete-rules.local.md from the templates
```

`install.sh` installs the skill **and** the downloader (`tools/fetch-attachments.py`) into
`~/.claude/skills/filemates/`, so everything is in one place; the tool reads the
`config.local.md` next to it. Then **edit** `~/.claude/skills/filemates/config.local.md`
and `reference/delete-rules.local.md` with your values, set up auth (see *Setup — mail
access*), restart Claude Code, and say e.g. *"check my email"* / *"triage my inbox"*.

(You can also just run the downloader straight from the cloned repo — it finds `config.local.md`
in either location.)

## Configure

Everything personal lives in two **git-ignored** files you create from the templates:

- `config.local.md` — your Gmail address, your Gmail-MCP tool prefix, your Reminders list
  name, your label map (topic → Gmail label) and folder map (attachment type → local
  folder).
- `reference/delete-rules.local.md` — what may be trashed, and the **protected senders**
  that must never be deleted. Fill the protected list first.

## Task backend (Apple Reminders by default)

Reminders/to-dos are the one piece that is platform-specific — and the least battle-tested, so
treat it as **best-effort** for now (it is being hardened separately). As written, the skill writes
to **Apple Reminders via AppleScript** — which is lovely on a Mac (it shows up in the
Reminders widget instantly, no app to open). That's why the skill is **macOS-first**.

The *concept* is backend-agnostic, though — the to-do step is just "create/read/complete a
task with a `[gmail:<id>]` tag in it". You can point it elsewhere by editing the Reminders
steps in `SKILL.md`, e.g.:
- **Things / Todoist / TickTick** — via their MCP or CLI,
- a plain **Markdown to-do file** in your notes,
- any task MCP you already use.

Honest status: only the Apple Reminders path is implemented and tested. Other backends are
**adaptable, not included** — swapping them is a small edit, but it's on you.

### Your task list + widget (recommended)

FileMates uses a **dedicated Reminders list** (default **`Email-Tasks`**, configurable via
`reminders_list`) — it's created during setup if absent, and FileMates only ever touches **that
list** (and only the reminders it created, tagged `[gmail:<id>]`). Your other lists and your own
manual reminders are never touched. Already use Reminders? This is just a separate second list.

**Recommended: add a desktop widget for `Email-Tasks`** — that's how you check tasks off (the
check-off *is* the "done" signal). FileMates can't place the widget for you (macOS only lets *you*
add widgets), and everything works without one — but the widget is the comfortable view.
*(macOS Sonoma/Sequoia; wording may vary slightly):*

1. Right-click an empty spot on the **Desktop** → **Edit Widgets**. *(Or click the date/time in
   the top-right menu bar → **Edit Widgets** for Notification Center.)*
2. Pick **Reminders** in the widget gallery.
3. Choose a size and drag it onto the Desktop (or Notification Center).
4. **Right-click the widget → Edit Widget → set List = `Email-Tasks`.**
5. Done. Tick a task in the widget when you've handled it (by email, transfer, phone, in person —
   however) → on its next run FileMates files/sorts the linked mail away.

## Setup — mail access (DO THIS FIRST; without it, nothing downloads)

The document-filing core needs read access to your mailbox over IMAP. Pick **one** auth
method and set it up before your first run. **OAuth is the recommended, proper path** — it
works on every account type (including Google Workspace), runs headless afterwards (no 2FA
prompts), and only touches a browser once during setup. The script auto-picks the method:
`MAIL_APP_PASSWORD` env var set → app password; otherwise → OAuth.

### Option A — OAuth ✅ recommended (and required for Google Workspace / business accounts)
Self-employed people usually have a **Workspace** (custom-domain) account, and Workspace
**blocks app passwords** — so OAuth is the way. A Google Cloud "project" here is just a free
registration that yields a client id/secret (an ID badge for your script); it stores **none
of your mail**. Auth runs locally; mail goes Gmail → your disk. **No billing account, free.**

1. **console.cloud.google.com** → create a project (e.g. `filemates`).
2. **APIs & Services → Library → Gmail API → Enable.**
3. **Google Auth Platform / OAuth consent screen → Get started** → User type **Internal**
   (works because you're the Workspace owner; no Google review) → app name + your email.
4. **Clients → Create client → Application type: Desktop app** → copy **Client ID** +
   **Client secret** into `config.local.md` (`oauth_client_id`, `oauth_client_secret`).
   (A *desktop* client secret is not a true secret per Google — but keep it local anyway.)
5. Run once: `python3 tools/fetch-attachments.py --auth-setup` → your browser opens, approve
   once → a refresh token is saved git-ignored in `tools/oauth-token.local.json`. From then
   on the script logs in **headlessly via XOAUTH2** — no 2FA prompts, ever.

### Option B — App password (personal accounts only, where it's still allowed)
For a **personal** Gmail/GMX/iCloud account with 2-Step Verification on, you can use
an app password instead of OAuth — never stored in the repo, only via env var:
```bash
export MAIL_APP_PASSWORD='xxxx xxxx xxxx xxxx'
```

### Option C — Browser (fallback, zero setup)
Don't want either? Drive Gmail in the browser (e.g. the Claude-in-Chrome extension) and save
the PDFs by hand. Works on any account, but slower and more fragile across Gmail
layouts/languages. Fine for a one-off backlog; **OAuth is the better long-term process.**

### IMAP hosts per provider
| Provider | `imap_host` | Port |
|---|---|---|
| Gmail / Workspace | `imap.gmail.com` | 993 |
| Outlook / Microsoft 365 | OAuth required — **not built yet** (see Limitations) | — |
| GMX | `imap.gmx.net` | 993 |
| iCloud | `imap.mail.me.com` | 993 |
| Fastmail | `imap.fastmail.com` | 993 |
| Own server | *(your host)* | 993 |

(The script finds Gmail's "All Mail" folder automatically, in any language.)

## Downloading attachments (once auth is set up)

```bash
# preview only — writes nothing:
python3 tools/fetch-attachments.py --from rechnungonline@telekom.de --since 2025-01-01 --type invoices --dry-run
# real run — files PDFs into the folder, leaves the mail untouched:
python3 tools/fetch-attachments.py --from rechnungonline@telekom.de --since 2025-01-01 --type invoices --folder "~/path/to/folder"
```

It downloads document attachments (PDF/DOCX/XLSX), renames them per your **naming scheme**
(`naming_scheme` in config — placeholders `<prefix> <type> <sender> <date> <DD-MM-YYYY> <original>`,
where `<date>`=ISO `YYYY-MM-DD` and `<DD-MM-YYYY>` (alias `<TT-MM-JJJJ>`) is day-first/German),
files them into the folder, and **verifies** them on disk.

- **Auto-delete (default, always reversible):** once an attachment is verified-filed, the mail
  is moved to the server's **recoverable Trash** — **only if** its sender is marked
  *delete-after-filing* in your delete-rules **and** is not on the protected list (so "download →
  file → name → delete" is one operation, e.g. for utility invoices). The tool **probes the
  server first** and picks a reversible mode (Gmail label, `MOVE`/`COPY` to Trash); if it finds
  no recoverable Trash it **leaves the mail in place** and tells you, rather than deleting
  permanently. Permanent removal happens only with the explicit `--force-expunge`. Disable all
  trashing with `--no-trash`; preview with `--dry-run`.
- **Folder guardrail:** if the target folder doesn't exist, the tool **errors** instead of
  creating it — pass `--mkdir` to create it deliberately. (This prevents a typo'd path from
  spawning a junk folder.)
- **Month-subfolder filing (folder placeholders):** folder paths (folder map or `--folder`)
  may carry `<YYYY>`, `<MM>` and `<MM_Monat>` (German month, e.g. `06_Juni`) — resolved **per
  attachment** from the same date the filename uses. No readable date → the attachment is
  reported and the mail **kept**, never guessed into a month. A typo'd placeholder (e.g.
  `<JJJJ>`) is refused outright — even with `--mkdir` it can never become a literal junk
  folder. See `config.example.md`.
- **Encrypted PDFs → quarantine:** a password/encryption-protected PDF can't be read (no
  date) and opening it can corrupt a downstream reader, so it's filed into a separate
  sub-folder (`protected_pdf_folder`, default `_Passwortgeschuetzt`) under the target, with
  its original name, and its mail is **kept** (not trashed — you may still need the password).
  Override the folder name with `--protected-folder`. Treat that folder as ignored: don't
  re-process or open its files.
- **Always `--dry-run` first** for batch runs: it shows the resolved folder, the matched
  count, and what would be filed/trashed — verify before the real run.

### Filename date — read it FROM the PDF (`date_source`)
By default the `<date>`/`<TT-MM-JJJJ>` placeholder is the **mail** date. For bookkeeping you
often want the date *inside the document* instead — set **`date_source`** in config (or
`--date-source` per run):

| `date_source` | uses | typical keywords (overridable) |
|---|---|---|
| `mail` (default) | the email header date | — |
| `abbuchung` | payment / charge date | "Date paid", "charged on" (German docs: "buchen wir am", "eingezogen am", "fällig am") |
| `rechnung` | invoice/issue date | "Date of issue" (German docs: "Rechnungsdatum", "Bestelldatum") |
| `leistung` | service period (end date) | „Leistungszeitraum", „Leistungsdatum" |

- The parser looks for a date **near** the keyword (DE + EN; formats `TT.MM.JJJJ`, ISO,
  „15. November 2025", „November 15, 2025"). Keywords are **user-overridable** via
  `date_keywords_abbuchung:` / `_rechnung:` / `_leistung:` (semicolon-separated).
- **Requires a PDF text layer.** It uses **`pdftotext`** (poppler; `brew install poppler`)
  if present, else **`pypdf`** (`pip install 'pypdf>=4.0'`; parsed bounded by a timeout), else it **falls back to the mail date
  and says so** (`--dry-run` prints the date source per file). Scanned/image-only PDFs without
  a text layer can't be read — they fall back too.

> Scope note: the **download** is provider-agnostic (IMAP/OAuth). The **triage/labelling**
> part of the skill currently depends on a Gmail MCP connector. Mixing is fine.

### OCR — make scanned PDFs searchable (`ocr`)
Many receipts (meal/expense scans, photographed invoices) are **image-only PDFs with no text
layer** — you can't search them and the date parser can't read them. Turn on OCR to add a text
layer after filing:

- Set **`ocr: auto`** in config (or `--ocr auto`). `auto` only OCRs PDFs that have **no** text
  layer; `yes` OCRs every PDF; `no` (default) is off. Language(s) via `ocr_lang` (e.g. `deu+eng`).
- **Backends (auto-selected):** on **macOS** it uses **Apple's built-in Vision OCR** — no
  Homebrew/tesseract needed, just Xcode Command Line Tools (`xcode-select --install`);
  `install.sh` compiles the tiny helper `tools/macos-ocr.swift`. On **Linux** it uses
  **`ocrmypdf` + `tesseract`** (`install.sh` installs via `apt`). If no backend is available,
  scans are still filed — just without a text layer — and the run prints a note. OCR runs
  **only after verified filing**; pages that already have text are left untouched (safe,
  re-runnable). The macOS Vision helper **auto-detects page orientation** (tries 0/90/180/270),
  so sideways/rotated scans come out upright and searchable. *Layout note:* dense multi-column
  tables may OCR imperfectly; plain receipts are reliable.
- **Bulk-OCR an existing folder** (e.g. years of receipt scans):
  `python3 tools/ocr-folder.py ~/Documents/Receipts --recursive` (add `--dry-run` first).
- *Note (Linux/tesseract path only):* `tesseract`/`leptonica` packages can occasionally be
  broken on very new OS releases (can't decode images) — then OCR is skipped and filing is
  unaffected. The macOS Apple-Vision path doesn't use tesseract and isn't affected.

## Safety model

- **Archive-/delete-only-when-safe:** answered or clear junk leaves the inbox; everything
  else stays + gets a reminder.
- **Verified filing:** an attachment mail is only archived after the file is confirmed on
  disk.
- **Protected senders:** matched first, always win, are never trashed (matched by exact
  address **and** domain, so an advisor's alias/sub-domain is covered too).
- **Trash, not delete:** deletion moves mail to the server's recoverable Trash (Gmail `TRASH`
  label = ~30-day recovery per Google's retention; other providers via `MOVE`/`COPY` to their Trash folder). There is
  no permanent deletion unless you explicitly pass `--force-expunge`; if the server offers no
  recoverable Trash, the downloader skips deletion instead of risking it.
- **Propose-first:** by default the skill lists deletion candidates and waits for your OK.
  You can switch to `auto` in the delete-rules file.

## Limitations (read this)

- **macOS only** (Apple Reminders / AppleScript). Not portable to Linux/Windows as written.
- **Attachment download uses IMAP + OAuth** (headless, reliable — set up once, see *Setup*).
  The Claude-in-Chrome browser path exists only as an optional fallback. If a download can't
  be verified, the skill leaves the mail in your inbox rather than archiving it.
- **Gmail MCP varies:** there is no single standard Gmail MCP; you must tell the skill your
  connector's tool prefix, and your connector must expose search/label/thread tools.
- **Tested & verified on Gmail; GMX verified up to dry-run.** The download→file→verify core is
  plain IMAP. Gmail is exercised live end-to-end (OAuth/XOAUTH2, incl. real filing + reversible
  trash). On a **real GMX account** (app password) the following was verified: login, the
  `--detect-delete` server probe (correctly picks reversible `move-trash` + GMX's localized
  Trash folder), search/match and the full `--dry-run` pipeline with no problems — a live
  write+trash cycle on GMX has not been run yet. Other app-password providers (Web.de, iCloud,
  Yahoo, Fastmail) *should* work the same way but are **not yet verified on a real account**.
  Microsoft 365 / Outlook need OAuth, which isn't built yet. If you use another provider:
  you're **very welcome to try it and report back** — see *Contributing & feedback* and
  `docs/PROVIDERS.md`.
- **Inline attachments are not filed.** A document sent as `Content-Disposition: inline`
  (some Apple Mail / forwarded mail) is reported under *Skipped*, not downloaded. As a
  safeguard, a mail with any unfiled inline document is **never auto-trashed** (it is kept,
  even if a sibling attachment filed) — so you never lose the only copy. Handle such mail
  manually.
- The inbox-hygiene / label layer (labels, archive) is **Gmail-specific**; the document-filing
  core is not.
- **Deletion across providers:** the Gmail path is exercised live. On GMX, the `move-trash`
  detection and dry-run preview were verified against the real server; the destructive step
  itself, like the other non-Gmail modes (`copy-trash` / `expunge`), is covered by the test
  suite with IMAP fakes but has **not** been executed against a real non-Gmail server yet.
  Reversible by design, but treat them as less battle-tested.
- **Protected-sender matching** is exact address + domain/sub-domain; it does **not** normalize
  `+aliases` or IDN/punycode domains. (Over-matching only ever *prevents* a deletion, so it fails
  safe — but list an advisor's exact alias if they use one.)
- **Linux OCR install** runs `sudo apt-get` non-interactively (the macOS path compiles a bundled
  Swift helper locally, no `sudo`). Set `NO_OCR=1` to skip it.

## Development & tests

The deletion path is safety-critical, so its invariants are locked down by tests
(`tests/test_delete_safety.py`) and run on every push via GitHub Actions
(`.github/workflows/ci.yml`, Linux + macOS):

```bash
python -m pip install pytest ruff
ruff check tools/ tests/
python -m py_compile tools/fetch-attachments.py tools/ocr-folder.py
python -m pytest tests/ -q
```

The tests prove the load-bearing safety rules: a filing failure never trashes mail,
the protected list always wins, deletion is only ever reversible, and an unscoped
`EXPUNGE` can never fire without `--force-expunge`.

## Privacy & disclaimer

- This skill processes your email **locally** through Claude Code and your own MCP
  connectors. It stores nothing itself except the attachments you tell it to file into your
  own folders.
- Gmail access runs through **your own** Gmail MCP connector; that connector's data flow
  and permissions are governed by its provider's terms and Google's API user-data policy —
  not by this skill.
- **Never commit real data.** `config.local.md` and `*.local.md` are git-ignored for this
  reason. Keep your real addresses, labels and protected list out of the repo.
- Provided **as is, without warranty** (see LICENSE). You are responsible for your own legal
  basis and for which connector you use. Destructive actions (archive, trash) are your
  responsibility — review the proposals.

## Contributing & feedback

This started as a personal tool and is shared as is — but feedback and fixes are genuinely
welcome, especially **provider reports**:

- **Tried it on a non-Gmail provider?** Please open an **issue** with what happened — provider,
  whether app-password login worked, whether deletion found a recoverable Trash, anything that
  broke. That is the single most useful contribution right now (see `docs/PROVIDERS.md` for the
  open providers). You don't need to write code.
- **Found a bug or have a fix?** Open an issue, or **fork** the repo, make your change, and send
  a **pull request** — it gets reviewed before anything is merged.
- Contributions are accepted under the project's license (FSL-1.1-MIT, inbound = outbound). Keep
  real addresses/credentials out of any PR (`*.local.md` is git-ignored for that reason).

See [CONTRIBUTING.md](CONTRIBUTING.md) for the short version.

## License

**FSL-1.1-MIT** (Functional Source License) — see [LICENSE](LICENSE).

Free to use, copy and modify for any **Permitted Purpose** (incl. your own internal/commercial
work) — you just may **not** offer it as a competing product or service (no reselling / hosted
SaaS). Two years after each version's release it automatically becomes **MIT**. Not OSI
"open source"; it is "fair source".

> 💛 Free to use — you never have to pay anything. Companies wanting a commercial/reselling
> license: get in touch via the repo's Issues.
