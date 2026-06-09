# Contributing to FileMates

FileMates started as a personal tool and is shared as is. Feedback and fixes are welcome —
no contribution is too small.

## The most useful thing right now: provider reports

FileMates is **tested and verified on Gmail only**. The download→file→verify core is plain
IMAP, so it *should* work on any provider that allows an app password — but that isn't verified
yet (see [`docs/PROVIDERS.md`](docs/PROVIDERS.md)).

If you use **GMX, Web.de, iCloud, Yahoo, Fastmail, a self-hosted server, or anything else**,
please open an **issue** and tell us:

- which provider + IMAP host you used;
- whether **app-password login** worked (or whether it forced OAuth);
- whether a **dry-run** (`--dry-run`) listed your mail correctly;
- whether deletion found a **recoverable Trash** (the run prints the detected `Deletion: …`
  mode) — or whether it refused;
- anything that broke, with the error text.

You don't need to write any code for this — it's the single highest-value contribution.

## Bugs & ideas

Open an **issue**. For anything touching deletion, please include the printed `Deletion: …`
line and whether you ran with `--dry-run`.

## Code changes (pull requests)

1. **Fork** the repo and branch from `main`.
2. Make your change. For anything in the deletion path, add/adjust a test in `tests/` —
   the safety invariants are covered by `tests/test_delete_safety.py` and
   `tests/test_main_integration.py`, and CI runs `ruff` + `pytest` on every push.
3. Run locally: `ruff check tools/ tests/` and `python -m pytest tests/ -q`.
4. Open a **pull request**. It is reviewed before merging — nothing lands unreviewed.

**Never include real data** in a PR (addresses, tokens, your protected/delete lists).
`*.local.md`, `*.local.json` and `oauth-token.local.json` are git-ignored on purpose.

## License of contributions

By contributing you agree your contribution is licensed under the project's license,
**FSL-1.1-MIT** (inbound = outbound). See [LICENSE](LICENSE).
