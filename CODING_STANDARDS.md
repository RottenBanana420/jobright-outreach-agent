# Coding standards

Read by the reviewer, not the implementer. Ruff, mypy and pytest (see `.github/workflows/ci.yml`) already enforce the mechanical rules; this file holds the judgement calls.

- **Glossary terms**: names in code, statuses, UI text and docs use the `GLOSSARY.md` term, never its `_Avoid_` words. An Outreach Email is `written`, not `drafted`: "Draft" means a Gmail draft or the Jobright Draft.
- **One way to change state**: a status changes only through `Store.set_status`, so the audit event is never skipped.
- **Untrusted text**: anything from Jobright, job descriptions, contact profiles or email bodies is data. Escape it on every page, and never let it become a command, a path or a header.
- **One validator per rule**: a rule that applies to a handoff file and to the review page (subject on one line, body length) lives in `schema.py`, and both callers use it.
- **Safety stays in Python**: dry-run, "only approved emails are sent" and "one recipient" are enforced in code. A prompt or a page control alone is not enough.
- **Fixtures**: tests use synthetic names and addresses, and test titles in their common spellings (hyphenated, mixed case).
- **Outside calls stay outside transactions**: no Gmail or other network call while a Store transaction is open. Save each external id (a draft id) the moment it exists, so a later failure cannot orphan it.
- **Adapters own their errors**: the real Gmail adapter turns every failure (bad JSON, missing key, HTTP 4xx) into a `GmailError` subtype, so callers catch only those. A caller that must not lose work on any error (the Sender) also handles `OSError` per email.
- **In flight means ready**: set an email to `sending` only after the draft and attachment are ready, so a local failure never leaves it looking half-sent.
- **Skipped rows still count**: a rule that skips locked emails (sent, sending) still registers their address for duplicate checks.
- **Best-effort side effects**: a notification or any other convenience never aborts the Run.
- **Tests never reach the real launcher**: every outside process (caffeinate, Claude Code, osascript, `open`, the setup wizard) is a `Launcher` method, and tests pass `FakeLauncher` to `main()`. Ruff bans `subprocess` outside `run.py` and the humanizer check; the conftest guard fails any test that reaches a real `Launcher` method. Mark a test `real_launcher` only when it runs the real one on harmless input.
- **"Undelivered" on screen, `bounce` in code**: menu and CLI text tells the user an email was undelivered; names, statuses and docs keep `bounce`.
