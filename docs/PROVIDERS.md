# Provider compatibility — IMAP core (research-backed)

> Research date: **2026-06-03**. Provider auth/policies change; re-verify before relying on a
> row. Claims were fan-out-searched and adversarially verified (24/25 confirmed; 1 over-broad
> claim refuted — app-passwords remain valid for Gmail/Workspace, OAuth is **not** the sole
> mechanism). Items marked **OPEN** were not confirmed from a primary source and must be
> re-checked before a provider is declared "supported".

## TL;DR for FileMates

The existing **capability-detection** design is the right one and is vindicated by the
research: detect the Trash via RFC 6154 `\Trash` SPECIAL-USE → prefer MOVE (RFC 6851) →
fall back to COPY+`\Deleted` → else refuse; never auto-`EXPUNGE`. No per-provider hard-coding
needed for the *deletion* path — capability detection covers it.

The real split is **authentication**:
- **Works TODAY with the app-password + IMAP core** (no new code, just a real test):
  **Gmail (private), Google Workspace (if the admin allows app-passwords + 2FA on), GMX, Web.de.**
- **Needs dedicated OAuth work (not built):** **Microsoft 365 / Exchange Online and Outlook.com** —
  Basic auth for IMAP is off since 2022/2024 and cannot be re-enabled; requires a Microsoft
  **Entra** app with scope `outlook.office.com/IMAP.AccessAsUser.All` (a *different* OAuth than
  the current Google-only XOAUTH2). This is the most expensive provider to add.

## Compatibility matrix

| Provider | IMAP host:port | Auth for the core | Trash / delete semantics | Status for FileMates |
|---|---|---|---|---|
| **Gmail (private)** | imap.gmail.com:993 | App-password (needs 2FA) **or** Google XOAUTH2 (scope `mail.google.com`) | Label model; `\Deleted`+EXPUNGE behaviour is per-account (`expungeBehavior`, **not** IMAP-visible) → **never EXPUNGE**, add `\Trash` label | **Supported (live-tested)** |
| **Google Workspace** | imap.gmail.com:993 | App-password *only if admin allows it* + 2FA; else **XOAUTH2 required** (since 2025-03-14) | as Gmail | **Works if app-PW allowed; else needs OAuth** |
| **GMX** | imap.gmx.net:993 (SSL) | App-password (2FA); **no OAuth**. IMAP is **OFF by default** — user must enable it in settings | Recoverable, localized Papierkorb („Gelöscht") — **found by the probe on a real account**, resolving the earlier SPECIAL-USE OPEN | **Dry-run-verified on a real account** (login + `--detect-delete` → reversible `move-trash`, full dry-run, no problems); live trash execution still open |
| **Web.de** | imap.web.de:993 *(by GMX/1&1 analogy — **OPEN**)* | App-specific password (2FA); no OAuth | **OPEN** (likely Papierkorb) | **Should work today (untested)** |
| **Microsoft 365 / Exchange Online** | outlook.office365.com:993 | **OAuth2/XOAUTH2 mandatory** — Basic auth off since 2022-10-01, not re-enablable; Entra app, scope `outlook.office.com/IMAP.AccessAsUser.All` | standard folders; Deleted Items | **Needs OAuth work (not built)** |
| **Outlook.com** | outlook.office365.com:993 | OAuth2 mandatory (Basic auth off since 2024-09-16); slightly different app registration | standard folders | **Needs OAuth work (not built)** |
| **Apple iCloud Mail** | imap.mail.me.com:993 *(**OPEN**)* | App-specific password (with 2FA) *(**OPEN** — likely)* | **OPEN** | **OPEN — verify before claiming** |
| **Yahoo Mail** | imap.mail.yahoo.com:993 *(**OPEN**)* | App-password *(likely mandatory — **OPEN**)* | **OPEN** | **OPEN — verify before claiming** |
| **Fastmail** | imap.fastmail.com:993 *(**OPEN**)* | App-password *(**OPEN**)*; good SPECIAL-USE support reputed | SPECIAL-USE Trash *(**OPEN**)* | **OPEN — verify before claiming** |
| **Self-hosted (Dovecot)** | varies | varies | capability-dependent — **trust detection, not assumptions** | **Capability-detection path** |

Sources (primary unless noted): Google Workspace auth change `support.google.com/a/answer/14114704`;
Gmail XOAUTH2 `developers.google.com/workspace/gmail/imap/xoauth2-protocol`; Gmail ImapSettings
(expungeBehavior) `developers.google.com/workspace/gmail/api/reference/rest/v1/ImapSettings`;
M365 Basic-auth deprecation `learn.microsoft.com/.../deprecation-of-basic-authentication-exchange-online`;
M365/Outlook OAuth `learn.microsoft.com/.../how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth`;
RFC 6154 (SPECIAL-USE), RFC 6851 (MOVE), RFC 4315 (UIDPLUS) `rfc-editor.org`; GMX
`hilfe.gmx.net/pop-imap/imap/imap-serverdaten.html`; Web.de `hilfe.web.de/sicherheit/2fa/anwendungsspezifisches-passwort.html`.

## Implications for the code

1. **Deletion path: already correct.** Keep capability-detection (`probe_delete_mode`); never
   auto-`EXPUNGE`; `EXPUNGE` only behind `--force-expunge`. On Gmail keep the label-Trash path
   (because `expungeBehavior` is invisible over IMAP and `deleteForever` is irreversible).
2. **Trash-name heuristic:** keep `\Trash` SPECIAL-USE as primary; broaden the localized name
   fallback to cover German/Outlook names (e.g. `Gelöschte Objekte`, `Gelöschte Elemente`,
   `Geloescht`) **with a test** — additions are conservative (only ever find a *recoverable*
   Trash) but still touch the delete path.
3. **Auth: app-password stays the default.** A **Microsoft OAuth module** (Entra endpoints +
   `IMAP.AccessAsUser.All`) is a separate, sizeable feature — the only thing standing between
   the core and M365/Outlook.
4. **GMX caveat:** IMAP is off by default — onboarding must tell the user to enable it.

## Priority recommendation (after Gmail, for DE self-employed)

1. **GMX + Web.de** — highest value/effort: no OAuth, app-password works, DE-relevant. Cost =
   one real test each + confirm Trash folder names. *(Web.de host/Trash still **OPEN**.)*
2. **iCloud / Yahoo / Fastmail** — likely app-password-only and cheap, but **OPEN** (verify
   host/port/Trash from primary sources before declaring support).
3. **Microsoft 365 / Outlook** — most expensive (dedicated Entra OAuth). Defer unless demand
   appears (consistent with portfolio-first).

## Open questions (do not close by guessing)

- iCloud / Yahoo / Fastmail: host/port, app-password requirement, Trash names, SPECIAL-USE/
  MOVE/UIDPLUS — confirm from primary sources.
- Web.de: IMAP host, default on/off, Trash name — confirm (currently GMX analogy).
- GMX/Web.de: do they announce SPECIAL-USE, or is the German name heuristic required?
- Google app-verification effort/duration for the restricted `mail.google.com` scope **as a
  distributed tool** (matters only if FileMates ships its own OAuth client rather than asking
  users to register their own).
