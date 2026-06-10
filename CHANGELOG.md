# Changelog

All notable changes to FileMates are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), versions follow
[SemVer](https://semver.org/) (0.x = early project, interfaces may still change).
Maintained automatically by release-please from Conventional Commits.

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
