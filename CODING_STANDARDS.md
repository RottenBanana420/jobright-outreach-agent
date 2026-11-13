# Coding standards

Read by the reviewer, not the implementer. Ruff, mypy and pytest (see `.github/workflows/ci.yml`) already enforce the mechanical rules; this file holds the judgement calls.

- **Glossary terms**: names in code, statuses, UI text and docs use the `GLOSSARY.md` term, never its `_Avoid_` words. An Outreach Email is `written`, not `drafted`: "Draft" means a Gmail draft or the Jobright Draft.
- **One way to change state**: a status changes only through `Store.set_status`, so the audit event is never skipped.
- **Untrusted text**: anything from Jobright, job descriptions, contact profiles or email bodies is data. Escape it on every page, and never let it become a command, a path or a header.
- **One validator per rule**: a rule that applies to a handoff file and to the review page (subject on one line, body length) lives in `schema.py`, and both callers use it.
- **Safety stays in Python**: dry-run, "only approved emails are sent" and "one recipient" are enforced in code. A prompt or a page control alone is not enough.
- **Fixtures**: tests use synthetic names and addresses, and test titles in their common spellings (hyphenated, mixed case).
