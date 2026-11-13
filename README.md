# Jobright Outreach Agent

[![Status: planning](https://img.shields.io/badge/status-planning-orange)](docs/project-brief.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Built with Claude Code](https://img.shields.io/badge/built%20with-Claude%20Code-D97757?logo=anthropic&logoColor=white)](https://claude.com/claude-code)
[![Gmail API](https://img.shields.io/badge/Gmail-API-EA4335?logo=gmail&logoColor=white)](https://developers.google.com/gmail/api)
[![SQLite](https://img.shields.io/badge/state-SQLite-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Human approval required](https://img.shields.io/badge/sends-only%20after%20approval-success)](#how-it-stays-safe)

A Claude Code agent that does the follow-up work after you apply for jobs on [Jobright](https://jobright.ai/). It finds the applications you submitted today, picks the ones most worth following up on, collects the recruiters and engineers Jobright suggests, and writes one personal email to each. Every email waits in your Gmail drafts until you approve it.

## Why

I'm a CS grad student applying for software engineering roles. Every application I submit should get a short note to a recruiter or hiring manager. Jobright Premium already shows the contacts and suggests a draft. The part that eats the evening is everything after that: opening each application, choosing who to write to, editing the draft so it mentions the actual role and something real from my resume, attaching the right resume, and keeping track of who I've already emailed.

On a busy day that's 30 to 40 emails, written after classes and assignments are done. This project hands the repetitive steps to an agent and leaves the decisions with me: which jobs to follow up on, who to contact, and which emails go out.

## What it will do

1. Open Jobright in your logged-in Chrome session through Claude in Chrome.
2. Find the applications you actually submitted today. Saved, viewed, or half-finished ones don't count.
3. Rank them by Jobright's match score, how well your resume fits, and whether the role is open to new grads, then show you the top 10 to approve or reorder.
4. For each approved application, pick up to four relevant contacts from Jobright's suggestions, recruiters first, then hiring managers and engineers on the team.
5. Start from Jobright's suggested email when one exists and tailor it to the role and your resume, in 80 to 140 words.
6. Create one Gmail draft per contact with the matching resume attached.
7. Show a review screen where you edit, approve, reject, or skip each draft.
8. Send only what you approved and record each send in a local SQLite log, so nobody gets the same email twice.

Planned commands:

```sh
python main.py --today     # run today's workflow, pausing for your approval
python main.py --dry-run   # preview everything, send nothing
```

## How it stays safe

- Nothing is sent without your explicit approval. Rejected, skipped, and unreviewed drafts never go out.
- Each email has exactly one recipient, with no CC or BCC.
- It only uses contacts and email addresses Jobright provides. It never guesses addresses or invents contacts to reach a quota.
- Emails don't claim experience, connections, or company research you don't have.
- It works through your own browser session and the official Gmail API. It doesn't touch cookies or tokens, and it doesn't try to get around CAPTCHAs or rate limits.
- Text read from job posts and profiles is treated as data. The agent won't follow instructions it finds inside a web page.

## Status

The project is in planning. The full requirements are in [docs/project-brief.md](docs/project-brief.md). Before writing the real code, the first milestone checks whether Claude in Chrome can read Jobright's dashboard, contacts, and suggested drafts.

| Milestone | Deliverable | State |
|---|---|---|
| 1. Feasibility | Report on what Claude in Chrome can read from Jobright | Next |
| 2. Application extraction | Today's confirmed applications as JSON | Planned |
| 3. Ranking | Ranked top 10 with reasons | Planned |
| 4. Contact discovery | Contact list with source links | Planned |
| 5. Email generation | Draft previews with attachments | Planned |
| 6. Gmail integration | OAuth and draft creation | Planned |
| 7. Approval workflow | Review screen and controlled sending | Planned |
| 8. Testing | Mock data, then test inboxes I own | Planned |

## Requirements

- Claude Code with the Claude in Chrome extension
- A Jobright Premium account, logged in to Chrome
- A Gmail account and a Google Cloud OAuth client
- Python 3.11 or later

## Disclaimer

This is a personal project and isn't affiliated with or endorsed by Jobright. Use it within Jobright's and Google's terms of service, and only send emails you'd be comfortable writing by hand.

## License

[MIT](LICENSE)
