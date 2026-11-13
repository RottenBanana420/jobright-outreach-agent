# Milestone 4: Contact discovery (trial)

Run on 2026-11-12, read-only, through Claude in Chrome, for the 5 Today's Applications from [Milestone 2](milestone-2-extraction.md). Nothing was sent and "Start Email" was never clicked. Output: `data/2026-11-12/contacts.json` (gitignored). Names and addresses stay out of the repo.

## Result

| Company | Beyond Your Network | Previous company | School | Recruiters | Hiring Leaders |
|---|---|---|---|---|---|
| Cursor | 5 | 1 | 4 | 1 (school group) | 0 |
| Affirm | 5 | 4 | 5 | 0 | 0 |
| Bask Health | 5 | 0 | 0 | 1 | 1 (Lead Engineer) |
| Foundation Health | 5 | 0 | 0 | 0 | 0 |
| Apple | 5 | 5 | 5 | 0 | 3 |

Every company has at least 4 usable Contacts, so the 4-per-company cap always binds.

## How contacts are exposed

- **All three contact groups are in the job page's data.** "Beyond Your Network" is a fixed list of 5. "From Your Previous Company" and "From Your School" sit in a separate block (`company` and `school` lists). Each Contact has name, title, listed company and LinkedIn URL. No email.
- **The email appears only in the "Connect Via Email" dialog** (email icon, then "Connect Now"). Opening the dialog does not send anything. The extractor has to open it once per chosen Contact, so it should pick the 4 Contacts first and reveal only those 4 addresses.
- **"Find More Connections" links to a LinkedIn people search.** Out of scope: LinkedIn is not a Jobright source.

## Findings

1. **The Jobright Draft is one template for every Contact Category.** The body for a Cursor engineer and for the Bask Health recruiter was identical apart from the name. Only the subject line is worth keeping. Settles an open question from [Milestone 1](milestone-1-feasibility.md).
2. **Recruiters are scarce.** 2 recruiters across 44 Contacts. The Contact Category priority will mostly fall through to Engineers, so the engineer email style is the one that matters most.
3. **The email domain can disagree with the company.** One Cursor Contact's revealed address was on another company's domain. The review page should flag any address whose domain doesn't match the company.
4. **Previous-company and school groups can include people outside the company.** Cursor's school group lists a Designer and an "AI Deployment Leader" with a blank company; one Beyond-Your-Network Contact also has a blank company. Decided: Contacts whose listed company is blank or different are left out.
5. **The same person can appear in two groups** (Cursor: previous company and school; Apple: previous company and school). Dedupe by LinkedIn URL before picking 4.
6. **Previous-company and school groups give a real shared connection** (same school or employer), which is the one genuine personal hook the email may use. The Jobright data names the school (`matchSchoolName`).
7. **Jobright may hold a custom resume per application.** Bask Health shows "View Custom Resume — Updated Nov 12, 2026". Decided: those stay with the applications; Outreach Emails always attach the user's own Resume Variant.

## Still not verified

- Whether revealing many addresses in a row is rate-limited (only 2 were revealed).
- Whether Beyond Your Network is ever more or fewer than 5.
