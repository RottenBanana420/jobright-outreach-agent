---
status: accepted
---

# Claude Code drives the browser; Python owns state and Gmail

Only a Claude Code session can call Claude in Chrome, so the daily run starts as a Claude Code command, not `python main.py --today`. Claude reads Jobright and writes the Outreach Emails into JSON files. A Python CLI handles everything that must be repeatable: ranking, SQLite state and deduplication, the review screen, and Gmail drafts and sending through the official Gmail API.

## Considered Options

- **Python drives the browser (Playwright on the user's Chrome profile).** Rejected: needs the user's cookies or a separate login, which the brief rules out, and breaks whenever Jobright's layout changes, where Claude adapts.
- **Gmail through the claude.ai Gmail connector.** Rejected for drafts and sending (see docs/research/gmail-mcp-vs-api.md): attaching the resume means the model writes the whole PDF as base64 output tokens for every draft. Kept for read-only checks such as spotting bounces and replies.

## Consequences

- The `--dry-run` guarantee lives in Python: the send command refuses to run without Approval rows in SQLite, whatever Claude does.
- Jobright reading can't run unattended on a schedule without a Claude Code session and an open Chrome.
