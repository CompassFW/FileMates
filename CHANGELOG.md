# Changelog

All notable changes to FileMates are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), versions follow
[SemVer](https://semver.org/) (0.x = early project, interfaces may still change).
Maintained automatically by release-please from Conventional Commits.

## [0.3.0](https://github.com/CompassFW/FileMates/compare/v0.2.0...v0.3.0) (2026-06-13)


### Features

* create --json — pass reminder candidates inline, no temp file ([744b820](https://github.com/CompassFW/FileMates/commit/744b820ea86ac6eb8760777b93f7bf12637df6ac))


### Bug Fixes

* clean usage errors for create --json + full-flag enforcement on subparsers ([e42ff0e](https://github.com/CompassFW/FileMates/commit/e42ff0e5707ed4631b550a5ca063821185fbcbbd))
* dedup reminders on the stable gmail-id, not just the LLM topic ([357576e](https://github.com/CompassFW/FileMates/commit/357576e0ea4fee321d3f841a42f475f12d4e2321))

## [0.2.0](https://github.com/CompassFW/FileMates/compare/v0.1.0...v0.2.0) (2026-06-10)


### Features

* waiting list for long-running cases (skip instead of re-asking) ([8cebc62](https://github.com/CompassFW/FileMates/commit/8cebc62a7899c64cb1e81c21b73cc4139141a869))

## 0.1.0 (2026-06-09)

Initial public release.

### Features

* IMAP attachment pipeline: download → rename (configurable scheme) → file → **verify on disk**, with byte/OCR-stable dedup and a quarantine for encrypted PDFs
* Month-subfolder filing via folder placeholders `<YYYY>` / `<MM>` / `<MM_Monat>` (resolved per attachment from the document date — never guessed)
* Naming overrides `--name-sender` / `--name-type` (real vendor + document type when mail arrives via a payment processor)
* Reminder helper for Apple Reminders: tested create/dedup/react core (task-level dedup, never an invented deadline, exactly-one-mail sort contract)
* Scheduled/unattended runs with a catch-up gate (`check-catchup` / `record-run`) so a slot slept through is caught up, never silently skipped
* Deletion safety model: "delete" always means the recoverable Trash, protected senders are never deleted, permanent expunge requires an explicit `--force-expunge`
* OCR text layer for scanned PDFs (Apple Vision on macOS, ocrmypdf on Linux)

VERSION baseline `0.1.0`; tagged on the initial-release commit.
