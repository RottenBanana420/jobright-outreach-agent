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

## The menu: `outreach` with no command

`./outreach` (a small script in the project folder that runs `uv run outreach "$@"`) or `uv run outreach`, in an interactive terminal, opens a Questionary select prompt. `outreach/menu.py` builds it from the Store, the config and the files (`build_menu`); the prompt itself is a thin layer on top. Nothing outside the project folder is changed: no aliases, no shell rc edits.

The title line is the date and today's status (today's Run is `data/<date>`): `no Run yet`, `a Run is in progress in another window`, `send interrupted: N emails to finish`, `waiting for your review: N emails`, `started, not finished; Start picks up where it left off`, `all sent: N emails` or `done, nothing sent`. Below it: the Gmail sign-in (`day N of 7`, or just `signed in` for a token saved before the date was kept) and the addresses recorded as bounced today, worded "N emails undelivered; those addresses won't be used again". User-facing text says "undelivered"; the code and these docs keep `bounce`. There is no item for recording bounces: Claude records them at the start of every Run.

One flat list, no sub-menus, no follow-up questions and no "are you sure" (the review page's totals confirmation is the gate). Daily items above the line, upkeep below. Items marked * show only when they apply.

| Item | Runs | Shows | Pointer starts here when |
|---|---|---|---|
| Start today's Run | `outreach start` | always | there is no Run today, or it stopped before the emails were written |
| Practice run (nothing is sent) | `outreach start --dry-run` | always | |
| Reopen the review page* | `outreach review data/<date>` | emails are waiting for review | emails are waiting |
| Finish an interrupted send* | `outreach send data/<date>` | confirmed emails are not all sent and labelled | a send was interrupted |
| Stop the Run* | `outreach stop` | a Run is live in another window | never (one Enter must not stop it; the pointer rests on Quit) |
| Finish setup* | `scripts/setup-wizard.sh` | `outreach.toml`, `credentials.json`, the Gmail token or a resume file is missing | setup is incomplete (this wins over everything) |
| Renew Gmail sign-in | `outreach gmail-auth` | always | from day 6 of the 7-day sign-in (before a send or review) |
| Show settings | `outreach config` | always | |
| Quit | | always (`q` also quits, as does Ctrl-C) | today's Run is done |

After an action returns (`start` included, during which Claude Code owns the terminal), the menu shows again with the new status. Ctrl-C during an action ends the action, not the menu. Every command keeps its arguments; the menu only calls them with the defaults (`data`, `data/outreach.db`, `outreach.toml`).

With no command and no terminal (stdin or stdout not a tty), `outreach` prints the usual help and exits 2. It never waits for input.

`outreach gmail-auth` saves `signed_in_at` (UTC, ISO 8601) in the token file. A token without it still loads.
