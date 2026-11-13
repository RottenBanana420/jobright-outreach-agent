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

Then it starts the review page on `127.0.0.1` (printing the URL) and runs until the final confirmation or Ctrl-C. `--no-serve` prepares and exits. `--dry-run` is stored on the Run: the page works fully, but confirming only checks the totals and marks nothing.

### Config

`resume_dir` and `[resume_files]` (Resume Variant to file name; keys `forward_deployed`, `ml`, `ai`, `data`, `software`; defaults in `outreach.example.toml`).

`humanizer_check`: optional path to the work-humanizer check script (`~/.claude/skills/work-humanizer/check.py`, not in this repo). When set and present, `prepare` and every page edit run `python <script>` with the email body on stdin. The real script always exits 0 and prints a `label: hits` line for each thing it flags (for example `semicolons: 2`, `AI words: ['delve']`), followed by stats lines even for clean text: `sentences: N, mean ...` and `words: N`. So only finding-shaped lines become `humanizer` findings: every line except `words: N`, and except `sentences: ...` unless it carries a `(uniform: ...)` note. A non-zero exit means the script crashed, so the check is skipped whatever it printed. Unset, missing, unable to start, or exiting non-zero means skipped, and `prepare` says so. Tests use a fake script that models this output, never the real one.

## SQLite

- `outreach_emails`: `run_id`, `contact_id`, `group_id` (unique together), `address`, `subject`, `body`, `content_hash`, `resume_variant`, `resume_path`, `block_reason`, `findings`, `status`, `created_at`.
- Statuses (only through `Store.set_status`, each with an `events` row): `written`, `approved`, `rejected`, `skipped`, then `ready_to_send` once the user confirms. The sender (#5/#6) takes it from `ready_to_send` to `sending`, `sent`, `failed` or `uncertain`. `LOCKED` in `emails.py` lists the statuses from `ready_to_send` on; the review page cannot change those.
- `events.kind` is `status` or `edit`. Edits and resume switches are `edit` events (the status columns repeat the current status).
- Editing or switching the resume of an approved email sends it back to `written`. `drafted`, the name used in the first version of this ticket, became `written` (the glossary avoids an unqualified "Draft"); an existing database is upgraded in place, rows and events, when opened.
- `runs.dry_run`.

## Review page (HTTP, localhost only)

Every shortlisted Company Group is listed, including one with no Outreach Email ("0 of N"), with its "N of M" shortfall. Contacts that get no email are listed under their group with the reason: cooldown (with the last-emailed date), address bounced before, or same address as another Contact. The Approval section says how many emails wait for review and how many of those have a blocked resume.

`GET /`, and `POST` (form-encoded, with the page's hidden `token`): `/emails/<id>/edit` (`subject`, `body`), `/emails/<id>/approve|reject|skip`, `/groups/<id>/resume` (`variant`), `/bulk-approve` (approves every `written` email that has no blocked resume; findings do not block Approval), `/confirm-send` (`emails` and `recipients`, which must equal the approved totals shown). Errors: 400 bad input, 403 bad token, Host or Origin, 404 unknown, 409 not allowed (blocked resume, locked email, totals mismatch, nothing approved).
