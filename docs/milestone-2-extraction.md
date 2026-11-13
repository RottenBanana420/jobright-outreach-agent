# Milestone 2: Application extraction

Run on 2026-11-12, read-only, through Claude in Chrome. Nothing on Jobright was clicked or changed. Output: `data/2026-11-12/applications.json` (gitignored).

## Result

5 Today's Applications, ordered by Match Score:

| # | Company | Role | Match Score | Notes |
|---|---|---|---|---|
| 1 | Cursor | Software Engineer, New Grad 2027 | 85 | |
| 2 | Affirm | Software Engineer, Early Career (SF) | 82 | |
| 3 | Bask Health | Software Engineer, Early Career | 80 | |
| 4 | Foundation Health | Software Engineer | 65 | Agent-Applied |
| 5 | Apple | Software Engineer, Ad Platforms | hidden | Job closed, ranked last |

All five map to the Software Engineer Resume Variant.

## How the list works

- **Sorted by Applied-on date, newest first.** Checked across the first 71 cards: dates never increase. Extraction can stop at the first card dated before today.
- **Virtualized infinite scroll.** Only the cards on screen exist in the page at any moment, and more load as the list scrolls. The extractor must scroll and collect, not read once.
- **Each job detail page embeds its data as JSON** (the page's own `__NEXT_DATA__` script): title, company, location, work model, seniority, responsibilities, required and preferred qualifications, core skills, closed flag, original job post link. Reading this is more reliable than parsing page text.

## Findings

1. **Use the Applied-list card score, not the detail-page score.** The detail page carries its own score that differs by 1–2 points (Bask Health 80 on the card, 82 on the detail page). The card is what the user sees.
2. **Closed jobs keep a score in the data** (Apple: about 75) even though the card hides it. The agreed rule (closed jobs ranked last) stands; the hidden score could break ties among closed jobs.
3. **Jobright's one-paragraph summary can be wrong.** Cursor's summary describes a different company. Emails must be written from the responsibilities and qualifications, never from the summary.
4. **Original job post links can carry the job ID in the query string** (Greenhouse). The extractor must keep the full URL.
5. **"Applied by Agent" was inferred from text order** (Foundation Health). The real extractor should read it from the card element itself.
6. **Detail pages also carry Jobright's internal ranking data and some profile data.** The extractor must take only the job fields listed above and discard the rest.

## Background mode

`claude -p --chrome` (headless, no window) drove Chrome successfully: it opened the Applied list, counted 5 applications dated today, and closed its tab. Background mode is viable.

The Claude Code sign-in on this Mac expires between 2026-11-16 and 2026-11-19. Interactive use asks to sign in again; unattended background Runs need a long-lived token from `claude setup-token`.

## Still not verified

- Behaviour when today's applications span more than one screen of the virtualized list (today's 5 fit in the first load).
- Whether the Applied-on date can differ from the local date late at night.
