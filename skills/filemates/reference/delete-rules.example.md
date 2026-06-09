# Delete rules — COPY to `delete-rules.local.md` and edit

> Controls what may go to the **Trash** (recoverable — Gmail keeps it ~30 days, other
> providers per their own Trash retention; never permanent unless you pass `--force-expunge`).
> Only what is explicitly allowed here is ever proposed for deletion. When in doubt: archive, don't delete.

## 🛡️ PROTECTED — never deleted (overrides everything)
If a mail matches, it is never trashed — at most archived.

**Senders (never delete):**
- your-tax-advisor@example.com
- your-lawyer@example.com
- your-bank@example.com

**Categories (never delete):**
- Tax / finance authority / VAT
- Business invoices & receipts with a retention obligation
- Contracts of any kind
- Insurance, authorities, court, bank/financing
- Client & project mail
- Personal mail from real people (not an automated sender)
- **Documents that exist ONLY as this email** — no portal, not re-downloadable (e.g. a
  garage/repair invoice, a one-off contract PDF). Download + file the attachment, but **keep
  the mail as your only backup** if your disk ever fails. Put such senders here, and do NOT
  add them to *delete-after-filing* below. (This is a recommended pattern — you decide which
  senders qualify; it is not hard-coded.)
- **Signed + unsigned versions of the same document → keep BOTH.** A draft/unsigned copy
  (e.g. a "Leseexemplar", review draft, or generated form) and its signed final version are
  treated as two documents, not duplicates. Never delete the unsigned one just because a
  signed one exists (and vice versa). When filing, disambiguate by name (e.g.
  `..._Entwurf` / `..._unsigned` vs `..._unterzeichnet` / `..._signed`). Byte-identical
  duplicates may still be de-duplicated; signed-vs-unsigned are NOT byte-identical.

## ❓ Unknown senders (no rule matches) — NEVER auto-delete
If a sender is on neither the protected list nor any delete rule:
- **Never trash automatically.**
- If the mail needs an action → create a reminder, leave it in the inbox.
- Otherwise → put it on the "uncertain" list and show it to the user.
- Only the user's decision adds the sender to the protected or delete list (the rules learn
  from confirmations). When in doubt, keep — never guess.

> Archive vs. delete: **answered** mail and **protected** senders are at most *archived*
> (labelled + removed from inbox), **never deleted**. Only what is explicitly listed below
> is ever moved to Trash.

## 🗑️ DELETE ALLOWED (trash) — only after proposal + confirmation

### 1. Clear junk
Newsletters, ads, marketing, spam, pure automated notices with no value and no document to keep.

### 2. Receipts after download + filing
Only if ALL are true: it is a pure receipt/confirmation (no open task, no question to you); the document was **verified** filed (Phase 4) or fully contained in the saved mail text; and there is no retention obligation beyond the filed file.

### 2a. Delete-after-filing (senders whose mail is trashed ONCE the attachment is filed)
For invoices that **also live in a portal / are re-downloadable** (utilities, SaaS) — once the
attachment is verified on disk, the mail is redundant. The downloader
(`tools/fetch-attachments.py`) trashes such mail **by default after verified filing**
(disable with `--no-trash`); the protected list always wins. Everything not listed here is
filed but kept.
- *(example — replace with a real address) your-utility [at] example.com — utility invoices that also live in a portal*
- *(example — replace with a real address) your-mobile-provider [at] example.com — phone bills*

> ⚠️ These are placeholders written with `[at]` on purpose so the tool does **not** treat them
> as real delete rules. Use a real `name@domain` address (no `[at]`) for your own entries.

> ⚠️ Only put a sender here if its document is **recoverable elsewhere** (portal, re-send).
> If the email is the *only* copy (see the protected "exists only as this email" category),
> keep the mail instead.

### 3. Sender delete-list (grows from your confirmations)
- *(empty — add senders as you confirm "always delete from this one")*
- *Pattern — your own invoicing/banking tool's notifications:* mails from your invoicing or
  banking provider (e.g. a Stripe/PayPal-style sender) that merely notify you about **invoices
  you issued** — "invoice RExxxx was paid", "you created/sent an invoice", or a "view invoice"
  link with no PDF attached — are redundant: the document already lives in that tool. Safe to
  delete, no download needed (even if the mail sits in a client label). **Exception — never
  delete** genuine dispute/chargeback/complaint threads from the same sender (real money
  matters in progress).
- *Pattern — SaaS/subscription "receipt" mails with no attachment and no direct download:* a
  payment/receipt notification that carries **no PDF attachment AND no direct file download**
  (only a "view receipt"/portal link that needs login) is not a usable record — the receipt is
  always re-downloadable from the provider's billing portal. Safe to delete (e.g. a
  Figma/Notion/Slack-style "receipt for your subscription" mail). **Only** keep/file it when a
  real attachment OR a direct file download is present. When in doubt, keep — and always exhaust
  every identification path (attachment, body, link text) before deleting.

### 3b. Mixed senders — only the marketing address, never invoices
Some companies send ads AND invoices. Only list their pure marketing addresses here; invoices come from other addresses (`billing@`, `reservations@`) and are off-limits — always check.
- *(example) marketing-address@somevendor.com — ads only; invoices come from billing@somevendor.com*

## ⚙️ Mode
Current mode: `propose`

- `propose` (default): the skill never deletes on its own — it lists candidates and waits for your OK.
- `auto`: junk + allowed receipts are trashed without asking; the protected list still applies.
