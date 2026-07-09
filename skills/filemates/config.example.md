# Configuration — COPY THIS to `config.local.md` and fill in your own values

> `config.local.md` is git-ignored. **Never put real personal data in `config.example.md`.**
> All values below are placeholders.

## Account
- `account_email:` you@example.com
- `gmail_mcp_prefix:` mcp__<your-gmail-connector>__   <!-- the fully-qualified tool prefix of YOUR Gmail MCP; tool names differ per connector -->
- `task_backend:` apple-reminders   <!-- only apple-reminders is implemented; see README "Task backend" for alternatives -->
- `reminders_list:` Email-Tasks   <!-- name of the Apple Reminders list to use -->
- `file_prefix:` DOC   <!-- prefix for filed attachment names, e.g. CF, ACME -->
- `ignore_attachments:`   <!-- OPTIONAL boilerplate skip-list: attachment filenames containing any of these (comma/semicolon-separated, case-insensitive) are NOT filed — standard beiwerk with no receipt value. Suggested: Datenschutzblatt, Anlagen, AGB, Reproduktion, Datenschutz. Empty = keep every attachment. Override per run with --keep-all. -->
- `protected_pdf_folder:`   <!-- OPTIONAL sub-folder name for ENCRYPTED / password-protected PDFs. When the downloader meets one it CANNOT read it (no date, and opening it can corrupt a reader), so instead of filing it normally it drops it into this sub-folder of the target, keeps the original filename, and does NOT trash the mail (you may still need it for the password). Empty = `_Passwortgeschuetzt`. Override per run with --protected-folder. RULE for any assistant: never open/read files in this folder — treat it as ignored. -->
- `ocr:` no   <!-- OPTIONAL: add a searchable text layer to SCANNED PDFs after filing (so receipts you only have as scans become searchable AND their date can be parsed). `no` (default) | `auto` (OCR only PDFs that have no text layer) | `yes` (OCR every PDF). OCR backend is auto-selected: macOS = Apple Vision (install.sh compiles a small Swift helper; needs Xcode CLT), Linux = ocrmypdf+tesseract (install.sh installs via apt). See README -> OCR. If missing, scans are still filed, just without a text layer, and the run says so. Override per run with --ocr. -->
- `ocr_lang:` deu+eng   <!-- OPTIONAL OCR languages (e.g. `deu+eng`, `eng`, `fra`). Used as-is by tesseract/ocrmypdf (Linux); auto-mapped to BCP-47 (deu->de-DE, eng->en-US) for Apple Vision (macOS). Override per run with --ocr-lang. -->
- `date_source:` mail   <!-- which date the <date>/<TT-MM-JJJJ> placeholder uses: `mail` (header date, default) OR read FROM the PDF: `abbuchung` (payment/charge date), `rechnung` (invoice date), `leistung` (service period, end). Needs `pdftotext` (poppler) OR `pypdf` installed; otherwise it falls back to the mail date and says so. Override per run with --date-source. -->
- `date_keywords_abbuchung:`   <!-- OPTIONAL: override the keyword list (semicolon-separated) the parser looks for, e.g. buchen wir am; eingezogen am; date paid; fällig am. Empty = sensible DE+EN defaults. Same for date_keywords_rechnung / date_keywords_leistung. -->
- `naming_scheme:` <prefix>_<type>_<sender>_<YYYY-MM-DD>   <!-- ACTIVE. Placeholders: <prefix> <type> <sender> <date> (=<YYYY-MM-DD>, ISO) <DD-MM-YYYY> (=<TT-MM-JJJJ>, day-first/German) <original>. Empty = this default. Tip: include <original> to keep the sender's own filename, e.g. to tell invoice vs receipt apart. E.g. day-first without prefix: <sender>_<type>_<TT-MM-JJJJ>. Note: <date>/<TT-MM-JJJJ> follow `date_source` — the payment/invoice/service date read FROM the PDF when set, else the mail date. -->

<!-- FOLDER placeholders: folder paths (folder map below, or --folder) may carry <YYYY>,
     <MM> and <MM_Monat> (German month, e.g. 06_Juni) for month-subfolder conventions like
     `~/Documents/Receipts <YYYY>/<MM_Monat>/`. They resolve PER ATTACHMENT from the same
     date the filename uses (the payment date when date_source is set, else the mail date).
     Strict: if no date is readable, the attachment is reported and the mail KEPT — the
     tool never guesses a month. A missing month folder errors unless you pass --mkdir. -->

## Schedule (OPTIONAL — automatic runs, fully user-configurable)
Empty = no automatic run (FileMates only acts when you invoke it). Set this up and the setup
step registers the runs with your host scheduler. **You personalize the times + how much an
unattended run may do.** Each user picks their own.
- `schedule_enabled:` no            <!-- yes | no. Default no — never auto-run without you opting in. -->
- `schedule_times:`                 <!-- comma-separated 24h local times, e.g. 09:00, 18:00 -->
- `schedule_account:` business      <!-- which account the scheduled run uses (only the configured one is touched) -->
- `schedule_mode:` collect          <!-- what an UNATTENDED run may do with your mail (no human present to confirm):
     report-only — scan + report only; create nothing, move nothing.
     collect     — create reminders for new actionable mail (additive, deduped); ANY mail move/delete is QUEUED for you.
     auto-sort   — also auto-SORT (reversible label+archive) on a checked-off task that resolves to exactly ONE mail + a mapped label; DELETE is ALWAYS queued for your confirmation, never unattended.
     (No mode ever deletes mail unattended. Unknown value fails safe to 'queue'.) -->

<!-- CATCH-UP: `schedule_times` are your REAL slots (e.g. 09:00 & 18:00). Host schedulers only
     fire while the app is open and skip a slot if the machine slept through it, so the setup
     registers MORE FREQUENT check ticks (e.g. 4×/day). Each tick runs a cheap gate
     (`reminder-helper.py check-catchup`) that runs the full flow only if a real slot is overdue
     and not yet done; otherwise it exits immediately (no inbox access). A missed slot is caught
     up within ~max-staleness (a fixed 12h — a slot crossed longer ago is not run pre-dawn). The
     last successful run is recorded in the git-ignored `.filemates-last-run.local.json` (the
     timestamp advances ONLY on a successful run, so a crashed run is retried, never silently
     skipped). You don't edit that file. -->

## IMAP (for the attachment downloader `tools/fetch-attachments.py`)
IMAP is provider-agnostic — only these values change per provider (see README table).
- `imap_host:` imap.gmail.com        <!-- Outlook/M365: outlook.office365.com · GMX: imap.gmx.net · iCloud: imap.mail.me.com · or your own server -->
- `imap_port:` 993
- `imap_user:` you@example.com        <!-- usually same as account_email -->
- `imap_mailbox:` [Gmail]/All Mail    <!-- Gmail: "[Gmail]/All Mail"; most others: "INBOX" -->
- `delete_mode:`        <!-- OPTIONAL, set ONCE at setup so later runs skip the per-run server probe (your provider doesn't change). Run `python3 tools/fetch-attachments.py --detect-delete` and paste the value it prints. One of: gmail-trash | move-trash | copy-trash | expunge | refuse. Empty = probe the server every run (safe fallback). All non-expunge modes are reversible (move to Trash); `expunge` is permanent and still needs --force-expunge. -->
- `trash_folder:`       <!-- OPTIONAL, goes with delete_mode=move-trash/copy-trash: the server's recoverable Trash folder name (e.g. Trash, Papierkorb, Deleted Messages). --detect-delete fills this in too. Ignored for Gmail. -->
- **Password:** NEVER put it here. Export it as the env var `MAIL_APP_PASSWORD` (use an *app password* for Gmail/Microsoft 365).

## OAuth (only if app passwords are blocked — e.g. Google Workspace/business accounts)
Leave these empty if you use an app password. If your account blocks app passwords, create a
free Google Cloud OAuth **Desktop-app** client (see README → "OAuth setup"), paste its values
here, then run once: `python3 tools/fetch-attachments.py --auth-setup`.
- `oauth_client_id:` <from Google Cloud Console>
- `oauth_client_secret:` <from Google Cloud Console>
<!-- The refresh token is stored git-ignored in tools/oauth-token.local.json — never committed.
     The Cloud project stores none of your mail; auth runs locally, mail goes Gmail -> your disk. -->
The script auto-picks: `MAIL_APP_PASSWORD` set → app password; else OAuth token → XOAUTH2.

## Label map (sender / topic → Gmail label)
Most specific sub-label wins. During first-run setup the skill runs `list_labels` and builds
this map *with* you: if you already have labels it adopts them (no duplicates); if you have
none it proposes + creates a starter set. Add `auto_create_labels: yes|no` below to say
whether it may create a missing sub-label at runtime or must ask first. Examples — replace:

- `auto_create_labels:` no

| Sender / topic | Gmail label |
|----------------|-------------|
| tax advisor, VAT | Admin/Tax |
| payroll | Admin/Payroll |
| phone/mobile bills | Admin/Phone |
| travel, hotels, train | Admin/Travel |
| hardware / software vendors | Admin/IT |
| client X | Clients/X |
| insurance | Car/Insurance |
| everything else business | Admin/General |

## Folder map (attachment type → local folder)
Use absolute paths. Examples — replace with your own:

| Type | Folder |
|------|--------|
| tax documents | ~/Documents/Company/Tax/ |
| invoices | ~/Documents/Company/Invoices/<Client>/ |
| receipts | ~/Documents/Receipts/<YYYY>/<MM>/ |
| contracts | ~/Documents/Company/Contracts/ |
| misc | ~/Documents/Company/Misc/ |

## Waiting list (long-running cases — leave their mails alone until YOU close the case)
Some cases stay open for days or weeks while you wait for an **external** outcome that never
arrives by mail — a chargeback you'll only see in your banking app, a refund, an authority's
decision. Without this list every run re-classifies those mails, may re-download their
attachments "to be safe", and asks you the same queue question again and again.

While a case is listed here, matching mails are **completely left alone**: no attachment
download, no reminder, no archive/trash, no repeated queue question. The report carries exactly
**one** summary line per case: `waiting: <case> (since <date>, N mails in inbox)`. Newly
arriving mails that match the case's matcher join it automatically. **Only the user closes a
case** (in chat — "case X is done"); only then does the *On close* column apply. Unattended
runs must NEVER edit this list themselves.

**Machine matcher (deterministic skip).** So a run does not re-read the prose every time,
`reminder-helper.py match-waiting` reads a compact token appended to each row:
`[[match ids=<id,…> senders=<addr or *@domain,…> exclude=<addr,…>]]`. `ids` are exact
gmail-ids; `senders`/`exclude` are case-insensitive globs matched against the sender address.
**`exclude` always wins** — a sender listed in `exclude` is never skipped by this case (the
safety net that keeps e.g. a monthly vendor receipt out of a same-vendor dispute). Put the
human prose first and the token last; a row without a token is matched by the model only.

| Case | Matcher (sender / subject / known IDs) | Since | On close |
|------|----------------------------------------|-------|----------|
| Chargeback dispute Vendor X (123 €) | from *@bank.example with subject dispute/chargeback; payment-provider receipts naming "Vendor X"; known IDs: aaa111, bbb222 [[match ids=aaa111,bbb222 senders=*@bank.example exclude=*@payments.example]] | 2026-06-01 | trash all case mails (attachments are already filed — do NOT re-download) |

## Delete rules
See `reference/delete-rules.example.md`. Copy it to `delete-rules.local.md` and edit. The protected-sender list is the most important part — put your tax advisor, lawyer, bank, authorities there so they are NEVER deleted.
