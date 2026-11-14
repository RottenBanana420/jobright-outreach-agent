# Starting, stopping and resuming a Run

Continues [review-handoff-files.md](review-handoff-files.md). The browser half of a Run is the project command `.claude/commands/outreach-run.md`; this file describes the Python side that launches it.

## `outreach start [--contacts N] [--dry-run] [--data-dir D] [--config] [--db] [--now]`

Typed by hand in a terminal, from the project folder. There is no schedule and no background mode. It:

1. creates the Run folder `D/<date>` (`D` defaults to `data`; the date is today in the configured timezone) and the Run row, or finds them again when this is a resume;
2. writes `run_state.json` in the Run folder (below) and marks the Run `running`;
3. starts `caffeinate -dis -w <pid of outreach start>`, so the Mac stays awake and the hold also ends if `outreach start` dies;
4. runs `claude --chrome --model <config model> "/outreach-run <run-dir>"` in the foreground, in this terminal, and waits;
5. when Claude Code exits, ends `caffeinate` and marks the Run `ended` (exit code 0) or `failed` (any other code; `outreach start` exits 1 and says to run it again).
6. while Claude Code runs, checks every 2 seconds whether the Run is `reviewed` (below). Once it is, Claude Code gets 30 seconds to print its final report, then `start` ends it and its child processes and marks the Run `ended`, whatever the exit code.

`prepare` and `review` mark the Run `reviewed` when the review page has closed (confirmed or stopped with Ctrl-C) and the confirmed emails have been sent. They do that only for a live Run (one with an `outreach start` process), so a review typed by hand never marks anything. A resumed Run starts again as `running`, so an earlier session's review does not end it.

`--contacts N` (at least 1) is the cap for this Run, as for `pick-contacts`. `--dry-run` is stored on the Run and passed to `prepare`. One Run at a time: a second `start` while another is in progress is refused.

## `outreach stop [--db]`

Ends Claude Code and its child processes and `caffeinate`, clears the Run's process ids and marks the Run `stopped`. `outreach start` in the other terminal then returns on its own. With no Run in progress it says so and exits 2. A Run whose `outreach start` process died without cleaning up is marked `failed` (and its `caffeinate` ended) the next time `start` or `stop` runs.

## `run_state.json` (`start` writes, the Run command reads)

```json
{"run_dir": "data/2026-11-12", "run_date": "2026-11-12", "dry_run": true, "contacts": null,
 "resumed": false, "steps_done": ["bounces", "applications"], "db": null, "config": null, "now": null}
```

- `steps_done`: the handoff files already in the Run folder (`bounces`, `applications`, `shortlist`, `contact_candidates`, `picked_contacts`, `revealed_addresses`, `written_emails`), plus `prepared` once the Run has Outreach Emails in SQLite. `applications`, `shortlist`, `contact_candidates`, `picked_contacts` and `bounces` are written once, complete. `revealed_addresses` and `written_emails` are written as Claude goes, so a stopped Run can hold a partial one; Claude fills in only the missing Contacts.
- `db`, `config`, `now`: the options `start` was given, which the command repeats on its own `outreach` calls.
- `draft_subjects.json` (`{"<contact_id>": "<Jobright Draft subject>"}`) is Claude's scratch file for the email step. Python never reads it. The Jobright Draft body is never stored.

## Resuming

`outreach start` again on the same day reuses the same Run folder and Run row. Steps whose files are complete are skipped, so Jobright is not read again and no email already written is written again. Once the Run has Outreach Emails (`prepared`), the command never runs `prepare` again, because `prepare` would put the user's edits and decisions back to the file's text. Instead (unless the Run is a dry run) it first runs `outreach send <run-dir>`, which sends only what the user already confirmed and exits 2 ("nothing to send") when nothing was confirmed, and then `outreach review <run-dir>` for whatever is still waiting for review.

A `stop` that arrives before Claude Code has been recorded (right after `start`) still takes effect: `start` records process ids only while the Run is not `stopped`, ends a Claude it just spawned, and launches nothing when the stop came first.

## `outreach review <run-dir> [--port N] [--config] [--db]`

Serves the review page for a Run whose emails are already prepared (exit 2 if there are none). Nothing is written to the emails.

## Opening the review page

Once the page is up, `prepare` and `review` open its URL in the default browser (`open <url>`) and post a Mac notification ("Outreach review ready", with the number of emails waiting and the page URL) through `osascript`. Both are best effort: macOS can hide the notification (for example when notifications for Script Editor are off), and the URL is also printed. Neither happens with `--no-serve`.

## SQLite

`runs.status`: `created`, `running`, then the stage markers (`shortlisted` ...), `reviewed` once the review page has closed during a live Run, and at the end `ended`, `failed` or `stopped` (each change with an `events` row). `runs.owner_pid`, `caffeinate_pid` and `claude_pid` are set only while a Run is live.
