# Design decisions

Settled during the design interview on 2026-11-12. Terms in bold are defined in [GLOSSARY.md](../GLOSSARY.md). Architecture is in [ADR 0001](adr/0001-claude-code-drives-browser-python-owns-state-and-gmail.md), the multi-role policy in [ADR 0002](adr/0002-one-outreach-email-per-contact-per-company.md).

## Running it

- `outreach start` is a terminal command, typed in a normal terminal tab. It starts `caffeinate` tied to the **Run**, opens Claude Code with Chrome enabled (visible mode: you can watch and step in), then hands off to the Python review page.
- `outreach stop` cancels a **Run** early. Otherwise the **Run** ends by itself once the review page has closed and the approved emails are sent, or once `outreach shortlist` finds nothing to follow up today: Claude Code gets 30 seconds for its final report, then `outreach start` ends it, and `caffeinate` exits with it.
- The user starts every Run by hand. No cron, no schedule, no always-on agent. One pass per start.
- Claude runs in visible mode: a terminal window shows its work and the user can step in. Headless mode was tested in Milestone 2 and works, but is not used.
- The only pause is email review. The review page opens in the default browser by itself when it is ready, with a Mac notification.

## Selecting applications

- **Today's Applications** only: status Applied, Applied-on date equal to today in America/Los_Angeles.
- Ranked by Jobright's **Match Score** alone, as shown on the Applied-list card. Closed jobs (no visible score) stay in, ranked last; the hidden score the job page still carries breaks ties among them (see [milestone 2](milestone-2-extraction.md)).
- **Shortlist** is at most 10 **Company Groups**. No approval pause on the Shortlist.

## Contacts and emails

- **Contacts** per **Company Group** is a config setting, `contacts_per_company`, default 4, minimum 1. It is a ceiling, not a target: a Company Group gets the setting or the number of eligible Contacts, whichever is smaller. With the setting at 10 and 7 eligible Contacts, 7 Outreach Emails are written. Nothing is invented to fill the gap.
- `outreach start --contacts N` overrides the setting for one **Run** without editing the config.
- Eligible means: from Jobright's suggestions only, deduplicated (same person in two groups counts once), and listed at that company. Contacts with a blank or different company are left out. The review page shows "7 of 10" when a Company Group falls short.
- All three Jobright groups are used: "Beyond Your Network", "From Your School" and "From Your Previous Company". Eligible school and previous-company Contacts are picked first, since the shared school or employer is a real connection the email can mention. Then the Contact Category order applies.
- **Unverified Addresses** are accepted; bounces are recorded so the address is not reused.
- One **Outreach Email** per Contact per Company Group, naming every role applied to there. 30-day cooldown flag for repeat Contacts.
- Emails are written from the job description, the resume and the **Jobright Draft**, 80–140 words, then passed through the project's `outreach-humanizer` skill (`.claude/skills/outreach-humanizer/`, a copy of phuryn/work-humanizer) in Rewrite mode, with its `check.py` as a lint.

## Resume Variant mapping

The Run reads copies kept in the project's `resumes/` folder (gitignored); the originals are in `~/Documents/Career/Resumes/Technical (2027 New Grad)/`. Copy a changed resume over by hand. First match on the role title wins:

| Order | Title contains | Resume Variant |
|---|---|---|
| 1 | Forward Deployed | `Kusai_Hajuri_Resume_Forward_Deployed_Engineer.pdf` |
| 2 | Machine Learning, ML, Data Scientist | `Kusai_Hajuri_Resume_ML_Engineer.pdf` |
| 3 | AI, LLM, GenAI, Applied AI | `Kusai_Hajuri_Resume_AI_Engineer.pdf` |
| 4 | Data Engineer, Data Platform, Analytics Engineer | `Kusai_Hajuri_Resume_Data_Engineer.pdf` |
| 5 | anything else | `Kusai_Hajuri_Resume_Software_Engineer.pdf` |

- Outreach Emails always attach the user's own Resume Variant. Jobright's per-application custom resumes are for applications only and are never attached.
- A Company Group with several roles uses the variant of its highest-Match-Score role.
- The review page shows the chosen variant and lets the user switch it for one email, for every email of a Company Group, or for several ticked emails at once.
- Before attaching: file exists, is a readable PDF, under 25 MB.

## Gmail

- Sender: the user's own Gmail address, set in the config file (kept out of the repo).
- Drafts and sends go through the Gmail API (see [research](research/gmail-mcp-vs-api.md)). The Gmail connector is used only for read-only checks for bounces and replies.
- Each draft has exactly one recipient, the Contact, with no CC or BCC.
- Sent Outreach Emails get the Gmail label "Outreach". Labels are mailbox-only; recipients never see them.
- For a live test, `test_recipient` sends every email to a plus-address of the user's own inbox instead of the Contact (see [review-handoff-files.md](review-handoff-files.md#live-test-mode-test_recipient)).
- Google Cloud and Gmail setup is walked through by `scripts/setup-wizard.sh`.

## Open, low priority

- Measure how much of the Claude plan's usage one **Run** consumes, on the first test Run.
- Compare Opus, Sonnet and Haiku on email quality and usage. Sonnet is the default.
