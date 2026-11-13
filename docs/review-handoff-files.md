# Review handoff files and `outreach prepare`

Continues [contact-handoff-files.md](contact-handoff-files.md). Same rules: JSON files in the Run folder, validated by `outreach` before use, rejected with a message naming the bad field (for example `emails[1].to[0]`). All text in them is untrusted: it is stored and shown (HTML-escaped), never interpreted.

## `revealed_addresses.json` (Claude writes, `prepare` reads)

The Unverified Address Jobright showed for each picked Contact. Only picked Contacts (see `picked_contacts.json`), identified by the SQLite `contacts.id`.

```json
{"addresses": [{"contact_id": 12, "address": "sam@acme.example"}]}
```

`contact_id` (a Contact of this Run, once each) and `address` (one email address, stored lowercase) are required.

## `written_emails.json` (Claude writes, `prepare` reads)

One humanized Outreach Email per Contact, naming every role in the Company Group.

```json
{"emails": [{"contact_id": 12, "to": ["sam@acme.example"], "subject": "Applied for Backend Engineer", "body": "..."}]}
```

- `to`: a list that must hold exactly the Contact's revealed address. More than one recipient, or any non-empty `cc` / `bcc`, is a hard error: `prepare` stores nothing and exits with code 2.
- `subject`: one line, at most 200 characters. `body`: at most 10000 characters.
- A Contact can have one email. A flagged (cooldown) Contact gets none.

## `outreach prepare <run-dir> [--dry-run] [--port N] [--no-serve] [--config] [--db] [--now]`

Needs `shortlist` and `pick-contacts` to have run on the folder. In one transaction it:

1. stores each revealed address (`Store.set_contact_email`);
2. skips a Contact whose address is in `bounces` (Contact status `bounced`) or equals an address already used for another Contact in the same file (status `duplicate_address`; the first wins). Both are reported on stdout;
3. creates or updates the Outreach Email for (Contact, Company Group), status `written`, with a SHA-256 content hash of `address\nsubject\nbody`. Running `prepare` again updates in place and puts the email back to `written`;
4. chooses the Resume Variant from the group's best role (open jobs by Match Score, then closed) with the first-match-wins table in [design.md](design.md), and checks the file: it exists, is at most 25 MB, starts with `%PDF-`, and ends with `startxref <offset> %%EOF` where the offset leads to an `xref` table or an `N G obj` cross-reference stream (stdlib check; a truncated or damaged file fails it). Title matching reads hyphens and underscores as spaces (`Forward-Deployed`, `Machine-Learning`), and `MLOps` counts as ML. A bad file does not fail `prepare`: the email is stored with a `block_reason` and cannot be approved until another Resume Variant is chosen;
5. lints the email (findings stored as JSON, shown on the page, do not block Approval): word count outside 80 to 140, an exact role title of the group missing from the body (one finding per missing role), no "applied", address domain not matching the company.

6. unless `--dry-run`, creates a Gmail draft for each email that has a usable resume (see [Gmail](#gmail-drafts-and-sending)). A dry run touches Gmail not at all and needs no `sender`.

Then it starts the review page on `127.0.0.1` (printing the URL) and runs until the final confirmation or Ctrl-C. `--no-serve` prepares and exits. `--dry-run` is stored on the Run: the page works fully, but confirming only checks the totals and marks nothing. After a real confirmation `prepare` sends straight away (below).

Running `prepare` again leaves emails that are already `ready_to_send` or later (sent, failed, uncertain) alone and says so.

### Config

`resume_dir` and `[resume_files]` (Resume Variant to file name; keys `forward_deployed`, `ml`, `ai`, `data`, `software`; defaults in `outreach.example.toml`).

`humanizer_check`: optional path to the work-humanizer check script (`~/.claude/skills/work-humanizer/check.py`, not in this repo). When set and present, `prepare` and every page edit run `python <script>` with the email body on stdin. The real script always exits 0 and prints a `label: hits` line for each thing it flags (for example `semicolons: 2`, `AI words: ['delve']`), followed by stats lines even for clean text: `sentences: N, mean ...` and `words: N`. So only finding-shaped lines become `humanizer` findings: every line except `words: N`, and except `sentences: ...` unless it carries a `(uniform: ...)` note. A non-zero exit means the script crashed, so the check is skipped whatever it printed. Unset, missing, unable to start, or exiting non-zero means skipped, and `prepare` says so. Tests use a fake script that models this output, never the real one.

## SQLite

- `outreach_emails`: `run_id`, `contact_id`, `group_id` (unique together), `address`, `subject`, `body`, `content_hash`, `resume_variant`, `resume_path`, `block_reason`, `findings`, `status`, `created_at`.
- Statuses (only through `Store.set_status`, each with an `events` row): `written`, `approved`, `rejected`, `skipped`, then `ready_to_send` once the user confirms. `outreach send` takes it from `ready_to_send` to `sending`, then `sent`, `failed` or `uncertain`. `LOCKED` in `emails.py` lists the statuses from `ready_to_send` on; the review page cannot change those.
- `events.kind` is `status` or `edit`. Edits and resume switches are `edit` events (the status columns repeat the current status).
- Editing or switching the resume of an approved email sends it back to `written`. `drafted`, the name used in the first version of this ticket, became `written` (the glossary avoids an unqualified "Draft"); an existing database is upgraded in place, rows and events, when opened.
- `outreach_emails` also holds `draft_id` (Gmail draft), `message_id` (the unique Message-ID header, `<hex@sender-domain>`), `gmail_message_id` and `sent_at` (the proof of a send) and `labelled`.
- `runs.dry_run`.

## Review page (HTTP, localhost only)

Every shortlisted Company Group is listed, including one with no Outreach Email ("0 of N"), with its "N of M" shortfall. Contacts that get no email are listed under their group with the reason: cooldown (with the last-emailed date), address bounced before, or same address as another Contact. The Approval section says how many emails wait for review and how many of those have a blocked resume.

`GET /`, and `POST` (form-encoded, with the page's hidden `token`): `/emails/<id>/edit` (`subject`, `body`), `/emails/<id>/approve|reject|skip`, `/groups/<id>/resume` (`variant`), `/bulk-approve` (approves every `written` email that has no blocked resume; findings do not block Approval), `/confirm-send` (`emails` and `recipients`, which must equal the approved totals shown). Errors: 400 bad input, 403 bad token, Host or Origin, 404 unknown, 409 not allowed (blocked resume, locked email, totals mismatch, nothing approved).

## Gmail drafts and sending

The Gmail adapter (`outreach.gmail.Gmail`) is the only external seam: `create_draft`, `update_draft`, `send_draft`, `find_sent` (by Message-ID) and `add_label`. `outreach.gmail_api.RealGmail` calls the official Gmail REST API (standard library only); tests use an in-memory fake with scripted failures. Both build the message with `build_mime`: From the configured `sender`, one `To`, no Cc or Bcc, the Resume Variant PDF read from disk (never through the model), a unique Message-ID.

### Setup (once)

1. In Google Cloud, create a project, enable the Gmail API, configure the OAuth consent screen, and create an OAuth client of type **Desktop app**. Save its JSON as `credentials.json` (gitignored) or point `gmail_credentials` at it. While the consent screen is in "Testing" Google expires the refresh token every 7 days; `outreach gmail-auth` then needs repeating.
2. Set `sender` in `outreach.toml` to the Gmail address.
3. `outreach gmail-auth` opens the browser, saves the token to `gmail_token` (default `data/gmail_token.json`, gitignored, mode 600) and prints the authorized address.

Scope: `https://www.googleapis.com/auth/gmail.modify`. It is the narrowest single scope that covers drafts, send, listing Sent and labelling: `gmail.compose` cannot list or label messages, and `gmail.labels` cannot label a message.

An expired or revoked token, or a missing one, stops with a message that says to run `outreach gmail-auth`.

### Drafts

- `prepare` (not dry-run) creates one draft per email. Each draft id is saved as soon as Gmail returns it, so a failure part way (auth, timeout) leaves the earlier drafts recorded and a second `prepare` carries on. A timeout on creation may leave a stray draft in Gmail that nothing records; it is never sent.
- An email with a blocked resume gets no draft until its Resume Variant is switched.
- Review page edits and resume switches update the drafts in the same step. If Gmail fails, nothing is saved and the page says so (HTTP 502). In a dry run they never touch Gmail.
- The sender refreshes the draft from the Store right before sending, so what was approved is what goes out, even if an earlier draft update failed half way.

### `outreach send <run-dir> [--config] [--db]`

Sends the emails the user confirmed (`ready_to_send`), one at a time, `send_pause_seconds` apart. `prepare` runs it by itself after the final confirmation; run it by hand to resume. It refuses (exit 2) for a dry run and when no email is confirmed and waiting. Rejected, skipped and unreviewed emails are never sent.

- Success records `gmail_message_id`, `sent_at`, status `sent` ("Gmail accepted it", not "delivered"), starts the Contact's cooldown, then applies the Gmail label `Outreach` (created if missing). A label failure does not undo the send; a rerun labels it.
- A timeout, dropped connection or 5xx answer marks the email `uncertain`, searches Sent for its Message-ID, and only if it is not there retries once (then searches again). Found means `sent`; no second send.
- An auth or quota error marks the email `failed` and stops the batch. Another refusal marks it `failed` and the batch carries on. Unsent emails stay `ready_to_send`.
- A rerun first searches Sent for every email left `sending`, `uncertain` or `failed`, then sends what is not there.
- Output lists what was sent and what is left. Exit codes: 0 everything sent, 2 refused or bad input, 3 something is left.

Not verified by automated tests: that Gmail keeps the custom Message-ID on `drafts.send`, which the Sent search relies on. Check it by hand against a test account before real outreach.
