# Jobright Outreach Agent

[![Status: working](https://img.shields.io/badge/status-working-success)](#status)
[![CI](https://github.com/RottenBanana420/jobright-outreach-agent/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Built with Claude Code](https://img.shields.io/badge/built%20with-Claude%20Code-D97757?logo=anthropic&logoColor=white)](https://claude.com/claude-code)
[![Gmail API](https://img.shields.io/badge/Gmail-API-EA4335?logo=gmail&logoColor=white)](https://developers.google.com/gmail/api)
[![SQLite](https://img.shields.io/badge/state-SQLite-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Human approval required](https://img.shields.io/badge/sends-only%20after%20approval-success)](#how-it-stays-safe)

A Claude Code agent that does the follow-up work after you apply for jobs on [Jobright](https://jobright.ai/). It finds the applications you submitted today, ranks them, picks the recruiters and engineers Jobright suggests at each company, and writes one personal email to each. Every email waits on a local review page until you approve it.

## Why

I'm a CS grad student applying for software engineering roles. Every application I submit should get a short note to a recruiter or hiring manager. Jobright Premium already shows the contacts and suggests a draft. The part that eats the evening is everything after that: opening each application, choosing who to write to, editing the draft so it mentions the actual role and something real from my resume, attaching the right resume, and keeping track of who I've already emailed.

On a busy day that's 30 to 40 emails, written after classes and assignments are done. This project hands the repetitive steps to an agent and leaves the decisions with me: which emails go out, what they say, and which resume they carry.

## How a Run works

You start a **Run** by hand with `uv run outreach start`. There is no schedule. The work is split in two ([ADR 0001](docs/adr/0001-claude-code-drives-browser-python-owns-state-and-gmail.md)):

- **Claude Code** drives Chrome through Claude in Chrome. You can watch it work and step in.
- **Python** (the `outreach` CLI) owns ranking, contact picking, resumes, linting, the SQLite log, Gmail and the review page.

The two halves pass JSON files through a Run folder (`data/<date>/`).

1. `outreach start` keeps the Mac awake with `caffeinate` and opens Claude Code on the `/outreach-run` command.
2. Claude reads Jobright's Applied list and keeps only applications marked Applied with today's date.
3. Python ranks them by Jobright's Match Score and keeps the top 10 companies. Several roles at one company form one **Company Group** and get one email per contact, naming every role ([ADR 0002](docs/adr/0002-one-outreach-email-per-contact-per-company.md)).
4. Claude collects the contacts Jobright suggests. Python picks up to 4 per company (configurable): school and previous-company connections first, then recruiters, hiring leaders and engineers. It skips anyone emailed in the last 30 days and any address that bounced before.
5. Claude reveals each picked contact's address and writes one 80 to 140 word email from the job description, your resume and Jobright's draft, then runs it through the project's humanizer skill.
6. Python checks every email, picks the matching resume PDF, lints the text, creates one Gmail draft per email and opens the review page in your browser.
7. On the review page you edit, approve, reject or skip each email and switch its resume if needed. Then you confirm the totals.
8. Python sends the approved emails one at a time through the Gmail API, labels them "Outreach" in Gmail, and the Run ends by itself.

A stopped or crashed Run picks up where it left off when you run `outreach start` again the same day. Nothing already read or written is redone.

## How it stays safe

- Nothing is sent without your explicit approval. Rejected, skipped and unreviewed emails never go out, and the final confirmation must match the number of approved emails and recipients.
- Each email has exactly one recipient, with no CC or BCC. Python refuses any file that says otherwise.
- It only uses contacts and addresses Jobright provides. It never guesses addresses or invents contacts to reach a quota.
- One email per contact per company, a 30-day cooldown for repeat contacts, and a bounced address is never used again.
- The resume PDF is read from disk by Python and attached through the Gmail API. The model never handles the file.
- A send that may or may not have gone through is checked against Gmail's Sent folder before any retry, so nobody gets the same email twice.
- The review page listens on 127.0.0.1 only and checks a token, the Host header and the Origin of every change.
- It works through your own browser session and the official Gmail API. It doesn't touch cookies, and it doesn't try to get around CAPTCHAs or rate limits.
- Text read from job posts and profiles is treated as data. The agent won't follow instructions it finds inside a web page.

## Setup

Requirements: macOS, [uv](https://docs.astral.sh/uv/), Claude Code with the Claude in Chrome extension, a Jobright Premium account signed in to Chrome, and a Gmail account.

```sh
uv sync
scripts/setup-wizard.sh
```

The wizard walks you through the steps only you can do: a Google Cloud project with the Gmail API, an OAuth client saved as `credentials.json`, `outreach.toml` with your Gmail address, the Gmail sign-in (`uv run outreach gmail-auth`), and the browser sign-ins Claude needs. Put your resume PDFs in `resumes/` (gitignored) under the names in [outreach.example.toml](outreach.example.toml).

While the Google consent screen is in "Testing", the Gmail sign-in expires every 7 days. The menu counts the days and points at "Renew Gmail sign-in" from day 6 (or run `uv run outreach gmail-auth`). To end the weekly sign-in, publish the Google app with `scripts/gmail-production-wizard.sh` ([why and how](docs/research/gmail-oauth-production-status.md)); it sets `gmail_publishing_status = "production"` so the menu stops counting.

## Usage

The quickest way in is the menu. In a terminal, from the project folder, run `./outreach` (or `uv run outreach`) with no command:

```
? Outreach · Fri 13 Nov · today: waiting for your review: 6 emails (↑↓ or number, Enter; q quits)
  Gmail: signed in, day 3 of 7
   1) Start today's Run
   2) Practice run (nothing is sent)
 » 3) Reopen the review page
     ─────
   4) Renew Gmail sign-in
   5) Show settings
   6) Quit
```

The pointer starts on the likely next step, so on a normal day one Enter starts the Run. Pick anything else with its number or the arrow keys, then Enter; `q` quits. Items that don't apply today are hidden. After the action finishes (a whole Run included), the menu comes back with the new status. [docs/run-files.md](docs/run-files.md#the-menu-outreach-with-no-command) lists every item and when it shows.

Every command still works on its own:

```sh
uv run outreach start              # today's Run
uv run outreach start --dry-run    # everything up to the review page, no Gmail drafts, nothing sent
uv run outreach start --contacts 2 # a different contact cap for this Run only
uv run outreach stop               # cancel the Run in progress, from another terminal
```

To try it on yourself first, set `test_recipient` in `outreach.toml` to your own address. Every email then goes to a plus-address of your inbox (`you+outreachtest<id>@gmail.com`) instead of the contact. Use a separate `--data-dir` and `--db` for the test, and empty the setting before real outreach.

Other commands, mostly called by the Run itself or used to resume by hand:

| Command | What it does |
|---|---|
| `outreach review <run-dir>` | Reopen the review page of a Run whose emails are already prepared |
| `outreach send <run-dir>` | Send the confirmed emails, or finish an interrupted send |
| `outreach gmail-auth` | Sign in to Gmail and save the token |
| `outreach config` | Print the effective settings |
| `outreach shortlist`, `pick-contacts`, `record-bounces`, `prepare` | The Python steps of a Run, one per handoff |

Settings are in [outreach.example.toml](outreach.example.toml). Copy it to `outreach.toml` (gitignored).

## Status

Working end to end. The full Run, from `outreach start` to labelled sent emails, has run against live Jobright and Gmail.

| Milestone | State |
|---|---|
| 1. Feasibility: what Claude in Chrome can read from Jobright | Done ([report](docs/milestone-1-feasibility.md)) |
| 2. Application extraction | Done ([trial](docs/milestone-2-extraction.md)) |
| 3. Ranking and Shortlist | Done |
| 4. Contact discovery and picking | Done ([trial](docs/milestone-4-contacts.md)) |
| 5. Email writing, linting and resume choice | Done |
| 6. Gmail drafts and controlled sending | Done |
| 7. Review page and Approval | Done |
| 8. Testing | 320 automated tests; live test to my own inboxes in progress ([#7](https://github.com/RottenBanana420/jobright-outreach-agent/issues/7)) |

Next up: packaging the agent so others can install it ([#26](https://github.com/RottenBanana420/jobright-outreach-agent/issues/26)).

## Development

```sh
uv run ruff check . && uv run mypy src tests && uv run pytest -q
```

CI runs the same sequence ([ci.yml](.github/workflows/ci.yml)). At runtime the code uses the Python standard library plus [Questionary](https://github.com/tmbo/questionary) for the menu. Contributor rules are in [CODING_STANDARDS.md](CODING_STANDARDS.md) and the project's vocabulary in [GLOSSARY.md](GLOSSARY.md).

## Docs

- [docs/design.md](docs/design.md): the settled design decisions
- [docs/adr/](docs/adr/): architecture decisions
- [docs/run-files.md](docs/run-files.md): `start`, `stop`, resuming and `run_state.json`
- [docs/contact-handoff-files.md](docs/contact-handoff-files.md) and [docs/review-handoff-files.md](docs/review-handoff-files.md): the files passed between Claude and Python, the review page and Gmail
- [docs/project-brief.md](docs/project-brief.md): the original idea

## Disclaimer

This is a personal project and isn't affiliated with or endorsed by Jobright. Use it within Jobright's and Google's terms of service, and only send emails you'd be comfortable writing by hand.

## License

[MIT](LICENSE)
