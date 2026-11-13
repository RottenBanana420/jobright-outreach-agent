# Contact handoff files

JSON files in the Run folder (`data/<date>/`, gitignored) between Claude and `outreach` for contact picking and bounces. `outreach` validates each file and rejects it with a message naming the bad field (for example `companies[0].contacts[1].group`).

## `contact_candidates.json` (Claude writes, `pick-contacts` reads)

Every Contact Jobright lists for each Shortlisted Company Group, from all three groups, with no email address. Do not drop blank-company or duplicate people; the picker handles both.

```json
{
  "companies": [
    {
      "company": "Acme",
      "contacts": [
        {
          "name": "Sam Example",
          "title": "Senior Software Engineer",
          "company": "Acme",
          "linkedin_url": "https://www.linkedin.com/in/sam-example",
          "group": "school",
          "connection": "Example University"
        }
      ]
    }
  ]
}
```

- `company` (top level): must match a Shortlisted Company Group (same normalization as `shortlist`), once each.
- `name`, `linkedin_url`: required. The LinkedIn URL is the person's identity; case, `www.`, query and trailing slash are ignored.
- `title`: optional; decides the Contact Category.
- `company` (per Contact): the company Jobright lists for the person. Blank or different from the group means never picked.
- `group`: `beyond_network`, `school` or `previous_company`.
- `connection`: optional; the shared school or employer name from Jobright's data. Only set it when Jobright says so.

## `outreach pick-contacts <run-dir>`

Options: `--contacts N` (this Run's cap, else `contacts_per_company`), `--config`, `--db`, `--now`. Needs `outreach shortlist` to have run on the same folder.

Rules: eligible by company, one Contact per LinkedIn URL (groups are merged), bounced people dropped, school and previous-company Contacts first, then Recruiter, Hiring Leader, Engineer, Other Recruiting (title keywords, checked in that order). The cap is a ceiling. A person emailed within `cooldown_days` at any company is flagged for review and does not use a slot of the cap.

Writes `picked_contacts.json`, which tells Claude whose addresses to reveal (only `contacts`, never `review`):

```json
{
  "run_date": "2026-11-12",
  "contacts_per_company": 10,
  "groups": [
    {
      "company": "Acme",
      "rank": 1,
      "picked_of_cap": "7 of 10",
      "resume_variant": "software",
      "resume_file": "/path/to/resumes/Kusai_Hajuri_Resume_Software_Engineer.pdf",
      "contacts": [
        {"id": 12, "name": "Sam Example", "title": "Senior Software Engineer", "category": "Engineer",
         "linkedin_url": "https://www.linkedin.com/in/sam-example", "groups": ["school", "previous_company"],
         "connection": "Example University", "cooldown_last_emailed": null}
      ],
      "review": []
    }
  ]
}
```

`resume_variant` and `resume_file` are the Resume Variant `prepare` will attach for the group (first-match-wins on its best role, see [design.md](design.md)). Claude reads that file to write the group's emails, so the emails and the attachment agree.

`review` holds cooldown-flagged Contacts, with `cooldown_last_emailed` set. `id` is the SQLite `contacts.id`.

## SQLite (for the address and email steps)

- `contacts`: one row per (Company Group, person), identity `linkedin_key`; `status` is `picked`, `flagged` or `dropped` (changed only through `Store.set_status`, with an event); also `category`, `jobright_groups`, `connection`, `position` (pick order), `cooldown_last_emailed`, `email`, `emailed_at`. Re-picking updates rows in place, so ids stay stable. A person who left the candidates is deleted, unless they already have an Outreach Email (deleting would cascade to it): they stay with status `dropped`, out of `picked_contacts.json`, and come back as `picked` or `flagged` if they reappear.
- `company_groups.contact_cap` and `contacts_picked`: the "N of M" figure.
- `bounces`: `address` (lowercase), `bounced_at`, `note`.
- Store methods the next steps use: `set_contact_email(contact_id, address)`, `mark_emailed(contact_id, iso_time)` (starts the cooldown), `is_bounced(address)` (check after revealing an address), `picked_contacts(group_id)`.

A bounced address blocks the person it was revealed for: the picker drops anyone whose stored `email` is in `bounces`.

## `bounces.json` and `outreach record-bounces <run-dir>`

Claude writes the bounce notices it finds in Gmail (read-only); `record-bounces` stores them. Repeating it is safe.

```json
{"bounces": [{"address": "sam@example.test", "bounced_at": "2026-11-10", "note": "mailbox full"}]}
```

`address` is required; `bounced_at` (a date) defaults to today; `note` is optional.
