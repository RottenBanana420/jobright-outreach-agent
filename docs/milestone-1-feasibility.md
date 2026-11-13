# Milestone 1: Feasibility report

Checked on 2026-11-12 through Claude in Chrome, in a new tab of the user's own Chrome, read-only. No email was created or sent and no Jobright record was changed.

## Capabilities

| Capability | Result | Notes |
|---|---|---|
| Claude in Chrome connected | Verified | Extension responded; tabs open and close normally. |
| Uses existing Jobright login | Verified | Already signed in on the Turbo plan. No credentials, cookies or tokens touched. |
| Navigate Jobright pages | Verified | `/jobs/applied`, `/jobs/info/<jobId>`, `/jobs/external` all load by URL. |
| Read the Applied list | Verified | Page text includes every card field listed below. |
| Applied-on date | Verified, date only | "Applied on Nov 12, 2026". No time of day, so "today" depends on Jobright's date matching the local date. |
| Distinguish submitted from saved/viewed | Partly | Applied, Liked (saved), and External (added by URL, not applied) are separate lists. Viewed and started-but-not-submitted jobs aren't listed anywhere. Jobright can't prove the ATS accepted the application: "Applied" means Jobright marked it applied. |
| Match Score | Verified | Overall % plus three sub-scores (experience, skills, industry or education). Hidden on cards marked JOB CLOSED. |
| Full job description | Verified | Detail page has responsibilities, required and preferred qualifications, skill tags, company facts, original job post link. |
| Contacts | Verified | Detail page → Insider Connection → "Beyond Your Network" → View. Sample job showed 5 contacts with name and title (one recruiter, several engineers). "From your previous company" and "From your school" were empty for that job. |
| Contact email | Verified, unverified quality | Clicking the email icon then "Connect Now" opens a dialog with the address. Jobright warns the address came from public information and may not be accurate. |
| Jobright Draft | Verified | Same dialog shows subject and body (700-character cap). |
| Bulk export or API | Not found | No CSV export seen; no public API assumed. |

## Fields available per Application

Company, title, location, employment type, work mode, seniority tag (New Grad, Entry Level, Mid Level), years of experience, salary range, Match Score and sub-scores, Applied-on date, status (Applied, Interviewing, Offer Received, Rejected, Archived), "Applied by Agent" label, "JOB CLOSED" label, H1B signal, Jobright job ID (from the URL), original job post link.

## Findings that change the design

1. **The Jobright Draft is a generic template.** The sample said the user "came across" the role and was "interested in applying", which contradicts an already-submitted application, and asked the recruiter to forward the resume. Its subject line is usable; its body is a weak starting point. Claude should write the body from the job description and resume, keeping only any genuinely specific detail from the Jobright Draft.
2. **Contact emails are inferred by Jobright.** Expect some bounces. Every address is an Unverified Address; bounces are recorded so a bad address isn't reused.
3. **Volume is lower than the brief assumed.** 5 Applications were dated today on the sample day, so the top-10 cut won't always bind.
4. **Closed jobs still appear in the Applied list** and lose their visible Match Score.
5. **Only Claude Code can drive Claude in Chrome**, so the daily run starts in Claude Code (see ADR 0001).
6. **The Gmail connector can attach files** but only by passing base64 content through the model, which is impractical for 30–40 PDFs. The Python Gmail API is the path for drafts.

## Not yet verified

- Whether the Applied list is sorted by Applied-on date and how it paginates (infinite scroll was not exercised).
- What Jobright's "Start Email" button does (not clicked: it may open a mail client or send).
- Whether Jobright Drafts differ per Contact Category or are the same template with the name swapped.
- Whether revealing contact emails is rate-limited (plan shows "Unlimited").
