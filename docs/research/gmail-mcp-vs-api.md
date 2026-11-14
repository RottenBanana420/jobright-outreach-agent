# Gmail: claude.ai connector (MCP) vs Gmail API

Researched 2026-11-12. Question: should Outreach Emails be created and sent through the Gmail connector already attached to Claude, or through the Gmail API from Python?

## What each one is

- **Gmail connector (MCP).** The claude.ai Gmail integration, exposed in Claude Code as `mcp__claude_ai_Gmail__*`. Anthropic holds the Google authorization. Relevant tools: `create_draft` (to, cc, bcc, subject, body, htmlBody, attachments) and `send_message` (can send an existing draft by `draftId`, returns the message ID). Only Claude can call it, from inside a Claude session.
- **Gmail API.** Google's REST API, called from our Python code with our own OAuth client. `users.drafts.create`, `users.drafts.send`, `users.messages.send`.

## The deciding fact: attachments

The connector takes an attachment as a base64 string inside the tool call. Tool-call arguments are written by the model, so every byte of the resume becomes model **output** tokens, on every draft.

Estimate for a 150 KB resume PDF (not measured: no API key in this environment to run `count_tokens`):

| | Per draft | Per day (35 drafts) |
|---|---|---|
| Base64 size | ~200,000 characters | ~7 M characters |
| Output tokens (at ~2–3 base64 chars per token) | ~65,000–100,000 | ~2.3–3.5 M |
| Cost at Opus 5.5 ($20 / M output) | ~$1.30–2.00 | ~$45–70 |
| Cost at Sonnet 5.5 ($10 / M output) | ~$0.65–1.00 | ~$23–35 |
| Cost at Haiku 5.5 ($0.50 / M output) | ~$0.03–0.05 | ~$1.10–1.75 |

On a Claude subscription the same tokens count against usage limits instead of dollars. Generating 65K+ tokens also takes minutes per draft, and one wrong character produces a corrupt PDF that nothing flags. Without attachments the connector costs a few hundred tokens per draft and is fine.

The Gmail API reads the PDF from disk in Python: zero model tokens, byte-exact.

## Comparison

| | Connector (MCP) | Gmail API |
|---|---|---|
| Setup | None, already connected | Google Cloud project, OAuth consent screen, desktop client, one browser consent |
| Resume attachment | Through model output tokens (above) | Read from disk, free |
| Who can call it | Only Claude, in a session | Python, so the send step is deterministic code |
| Duplicate-send protection | Depends on the model following instructions | Code checks SQLite before each send |
| Message IDs for the audit log | Returned to Claude, which must record them | Returned to Python, written directly |
| Re-authorization | Handled by claude.ai | In "Testing" status Google expires the refresh token every 7 days (one consent click a week). Publishing the app to "In production" without verification ends that for personal use, at the cost of a click-through "unverified app" warning and a 100-user cap. Sourced details and console steps: [gmail-oauth-production-status.md](gmail-oauth-production-status.md). |
| Scopes | Whatever claude.ai was granted | `gmail.modify` (drafts, send, search Sent, labels), which Google classifies as Restricted. The first plan was `gmail.compose`, also Restricted, but it cannot list or label messages. |
| Quotas | Not documented | 6,000 units/min/user. Draft create 10 units, send 100. |
| Daily send limit | Gmail account limit, ~500 recipients/day (third-party sources) | Same |

## Recommendation

Use the **Gmail API** for creating drafts and sending, because the resume must be attached to every email. Keep the **connector for read-only checks** where it costs little: looking for bounce notices and replies after a send, without our app needing a read scope.

If the resume were sent as a Google Drive link instead of an attachment, the connector would be the simpler choice.

## Sources

- Google OAuth 2.0 overview, refresh-token expiry: https://developers.google.com/identity/protocols/oauth2
- Gmail API scopes and classifications: https://developers.google.com/workspace/gmail/api/auth/scopes
- Gmail API usage limits: https://developers.google.com/workspace/gmail/api/reference/quota
- Unverified apps: https://support.google.com/cloud/answer/7454865
- Gmail daily sending limits (third-party summaries): https://www.swarmhit.com/blog/gmail-sending-limits, https://reply.io/blog/gmail-sending-limits/
- Claude model pricing: claude-api skill model table, cached 2026-11-03
