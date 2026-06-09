# Security Policy

FileMates touches sensitive things — your mailbox, OAuth tokens / app passwords, and the
deletion of mail — so security reports are taken seriously.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.** Use GitHub's private
vulnerability reporting instead:

> Repo → **Security** tab → **Report a vulnerability** (private advisory).

Include what you found, how to reproduce it, and the impact. You'll get a response as soon as
reasonably possible. Please give a reasonable window to fix and release before any public
disclosure.

## Supported versions

This is an early project; only the latest `main` is supported. Fixes land on `main`.

## What's in scope (the security-relevant surface)

- **Credential handling** — `MAIL_APP_PASSWORD` (read from the environment only, never
  persisted) and the OAuth flow / refresh token.
- **The deletion path** — anything that could make FileMates delete mail it shouldn't, or
  delete irreversibly.
- **Injection surfaces** — the IMAP command construction and the generated AppleScript
  (`osascript`) for Apple Reminders.
- **The OAuth loopback flow** (`--auth-setup`).

## Security model (how it's designed to be safe)

These are the invariants FileMates is built around — a regression in any of them is a security
bug worth reporting:

- **"Delete" always means the recoverable Trash, never a permanent wipe.** Mail goes to the
  provider's Trash (recoverable for the provider's retention window, ~30 days on Gmail). A
  permanent `EXPUNGE` is **refused** unless you explicitly pass `--force-expunge`; no automated
  flow ever sets it. Files/folders go to the OS Trash, never `rm`.
- **Protected senders are never deleted** (put your tax advisor, bank, lawyer, authorities on
  the protected list).
- **Delete only after verified filing**, and the default is propose-first — rule-covered trash
  (your own delete-list) is the only thing that auto-trashes, and only reversibly.
- **Credentials never touch the repo.** App passwords are env-only; the OAuth refresh token is
  written only to the git-ignored `tools/oauth-token.local.json` (mode `0600`) and never logged.
  All config with personal data lives in git-ignored `*.local.*` files.
- **Local-only data flow.** Your mail goes Gmail/IMAP → your own disk. No third-party server
  receives your mail or credentials.

## Good practice for users

- Use an **app password** or a dedicated **OAuth desktop client** — never your main password.
- Keep your `*.local.*` config files out of any repo you publish.
- Run with `--dry-run` first when trying new delete rules.
