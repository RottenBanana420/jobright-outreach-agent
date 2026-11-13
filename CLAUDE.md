## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (`RottenBanana420/jobright-outreach-agent`) via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Checks

Lint, typecheck and tests run as one sequence in `.github/workflows/ci.yml`. Run it with `uv run` before committing.

## Review

Reviewers check the diff against `CODING_STANDARDS.md`, `GLOSSARY.md` and `docs/adr/`.

## Handoff files

Before reading or writing a file that passes between Claude and the CLI (applications, contact candidates, picked contacts, revealed addresses, written emails, bounces), read the shapes in `docs/contact-handoff-files.md` and `docs/review-handoff-files.md`. The latter also holds the humanizer check contract.
