# Testing the Run's failures against the real world

Researched 2026-11-14. Question: how can each failure the Run handles (the table in "When something goes wrong" in `.claude/commands/outreach-run.md`, plus usage limits, context exhaustion and the user quitting) be tested against the real services instead of fakes, safely, on the user's own Mac?

Every claim about an outside system links to its owner's page (Google, Anthropic, Apple, Chrome, Python, Playwright, the IETF). Claims about this project cite the file. Where the sources are silent the note says so, and inferences are marked **(inference)**. Claude Code facts were checked against v2.1.323, the version installed on 2026-11-14.

## Answer

| Failure | Classification | Who does the injecting step |
|---|---|---|
| Signed out of Jobright | real | user signs out (and back in) |
| Chrome is not connected | real | user quits Chrome or turns the extension off |
| Jobright blocked the Run (CAPTCHA, rate limit, error page) | error page: real; CAPTCHA and rate limit: replayed captures only | user (DevTools) |
| Jobright's page changed | replayed captures only | user saves pages; Claude mutates copies |
| Gmail connector not connected | real | user disconnects and reconnects in claude.ai |
| Gmail sign-in expired (Python token) | real, on a copy of the token | Claude edits the copy; a true revoke is the user's |
| Gmail is not answering (Python) | real, for one process | Claude (proxy env var); Wi-Fi off is the user's |
| Gmail sending limit | fakes only | nobody: never hit it for real |
| A file was rejected | real | Claude seeds a bad file in a test Run folder |
| Claude Code usage limit | observe only when it happens; not inducible safely | nobody |
| Context exhaustion | observe only; not inducible cheaply | nobody |
| The user quitting Claude | real | user |

Three findings shape the plan:

1. **A practice run never touches the Python Gmail adapter.** "A dry run touches Gmail not at all" (`docs/review-handoff-files.md`, `prepare` step 6; `_prepare` and `_serve_review` in `src/outreach/cli.py` only build `RealGmail` when not a dry run). So "Gmail sign-in expired" and "Gmail is not answering" from the Python side cannot be met in a `--dry-run` Run. Test them on the adapter directly, with a test config pointing at a copy of the token, using a read-only call.
2. **`outreach halt` needs the model.** Claude ends a Run by running `outreach halt` (`.claude/commands/outreach-run.md`, "When something goes wrong"). When Claude Code itself cannot reach Anthropic's API (Wi-Fi off) or is at a usage limit, Claude cannot run that command, so these cases are not "handled" by the Run at all; Claude Code's own behaviour is what the user sees (sections below).
3. **Claude in Chrome's own defaults differ from the Run command.** Anthropic's docs say "When Claude encounters a login page or CAPTCHA, it pauses and asks you to handle it manually" ([Use Claude Code with Chrome](https://code.claude.com/docs/en/chrome)), and with the extension missing Claude Code may show an install prompt that waits for input ([same page, "Install the extension when Claude asks"](https://code.claude.com/docs/en/chrome#install-the-extension-when-claude-asks)). The Run command tells Claude to halt instead. Only a real test shows which wins.

## Ground rules for every test

- **Test copies, not the real files.** Every `outreach` command takes `--config` and `--db`, and `start` takes `--data-dir` (`docs/run-files.md`). Use `data/test/` (gitignored by `data/` in `.gitignore`) for a test config, a test database and the Run folders. `gmail_token` in the config chooses the token file (`src/outreach/config.py`), so a test config can point at `data/test/gmail_token.copy.json` and the real `data/gmail_token.json` is never written.
- **Practice runs only.** `outreach start --dry-run` stores the flag and passes it to `prepare`; nothing is drafted or sent (`docs/run-files.md`; `docs/review-handoff-files.md`).
- **One test Run per day per data dir.** A Run folder is `D/<date>` and a second `start` the same day resumes it (`docs/run-files.md`, "Resuming"). Use a fresh `--data-dir` (for example `data/test/a`, `data/test/b`) per scenario, or delete the test folder and test database between scenarios.
- **Who does what.** Claude may not enter passwords, change system or security settings, or solve CAPTCHAs. So the user signs in and out, quits Chrome, toggles extensions and Wi-Fi, and disconnects or reconnects connectors. Claude prepares test configs, copies and mutates fixtures, and reads the results. The user types `outreach start` (a Run is "typed by hand in a terminal", `docs/run-files.md`).
- **What to check after each test.** The terminal line `outreach: the Run stopped: <problem>` and exit 1 (`start_run` in `src/outreach/run.py`), the Run's status `halted` in the test database, and the menu title `stopped: <problem>` (`docs/run-files.md`, "The menu"). Then fix the cause and `outreach start` again with the same options to check the resume.

## Each failure

### Signed out of Jobright (real)

How to reproduce: the user signs out of jobright.ai in the Chrome profile that has the extension, or deletes jobright.ai's site data, then starts a practice run. The command expects a sign-in page at `https://jobright.ai/jobs/applied` and halts with "Signed out of Jobright" (`.claude/commands/outreach-run.md`, step 2.1). Afterwards the user signs in again; Claude must not.

A separate Chrome profile is the cleaner alternative: profiles keep bookmarks, history, passwords and "other settings" separate and can each have their own sign-in ([Google Chrome Help: Use Chrome with multiple profiles](https://support.google.com/chrome/answer/2364824)). Two caveats. That page does not say that extensions are per profile, so the extension would have to be installed and signed in to claude.ai in the new profile **(inference: Chrome extensions are installed per profile; not stated on that page)**. And with two connected browsers Claude Code asks which one to use, and "keeps using your choice even when another browser connects" ([Use Claude Code with Chrome](https://code.claude.com/docs/en/chrome)); pick the test profile through `/chrome` > **Select browser…**, and switch back afterwards. Signing out and back in is simpler.

What to watch: whether Claude halts (the command) or pauses and asks the user to sign in (Claude in Chrome's documented default, see finding 3).

### Chrome is not connected (real)

How to reproduce, by the user, before or during a practice run:

- quit Chrome (Cmd-Q); or
- turn the Claude in Chrome extension off in `chrome://extensions`.

What Anthropic documents: Claude Code "starts normally when Chrome isn't running". The browser tools then fail with "Browser extension is not connected" (extension not running, or signed in to another account or organization) or "No browsers are connected" (Chrome not open with the extension signed in), and a disabled extension shows "Not detected" in `/chrome` ([Use Claude Code with Chrome, Common error messages](https://code.claude.com/docs/en/chrome#common-error-messages)). The extension's service worker can also go idle in long sessions ("Receiving end does not exist"), fixed by `/chrome` > "Reconnect extension" (same page, "Connection drops during long sessions"); that is a third real way to meet this failure, but not one that can be caused on purpose.

The install prompt: when Claude Code "doesn't detect the extension" in an interactive session it shows "Claude wants to use your browser", at most once per session ([same page](https://code.claude.com/docs/en/chrome#install-the-extension-when-claude-asks)). The docs do not say whether a session started with `--chrome` and the extension merely turned off shows this prompt. If it does, the Run waits for the user instead of halting. Test with the extension off **at start** and, separately, Chrome quit **mid-Run** (after step 2 has begun).

The Claude in Chrome help centre article does not say what happens when Chrome closes or the extension is off ([Getting started with Claude in Chrome](https://support.claude.com/en/articles/12012173-getting-started-with-claude-in-chrome)).

### Jobright blocked the Run (error page real; CAPTCHA and rate limit replay only)

A CAPTCHA or rate limit cannot be caused on purpose without hammering Jobright, which is neither safe for the user's account nor something Claude should do. Jobright publishes no documentation on its limits (none found). So:

- **Error page, real.** Chrome DevTools can put a tab Offline ("select Offline from the Network throttling drop-down menu") and can block chosen requests from the Network request blocking tab ([Chrome DevTools Network features reference](https://developer.chrome.com/docs/devtools/network/reference)). Either gives the tab Chrome's real error page for jobright.ai.
- **CAPTCHA or rate-limit page, replay.** DevTools Local Overrides let you "Override content" of a response; overrides are saved to a folder and kept "across page loads" ([Override web content and HTTP response headers locally](https://developer.chrome.com/docs/devtools/overrides)). The user can override the Applied page's document with a saved copy edited to look like a block page.

Caveat for both: DevTools settings apply to the tab whose DevTools is open **(inference; the pages do not say so explicitly)**, and Claude works in a new tab it opens itself ([Use Claude Code with Chrome](https://code.claude.com/docs/en/chrome)). The user would have to open DevTools in Claude's tab once it appears, and the pages do not say whether DevTools and the extension can be attached to the same tab at once. Treat this as an experiment that may not work; if it does not, fall back to the replay harness under the next heading.

### Jobright's page changed (replay of real captures only)

A real layout change cannot be caused. What can be done:

1. **Capture.** The user, signed in, opens the Applied list and one job detail page with DevTools open and exports a HAR. DevTools exports a "sanitized" HAR by default, without sensitive headers; a HAR with them needs a setting turned on ([Network features reference](https://developer.chrome.com/docs/devtools/network/reference)). Use the sanitized export. The detail page's HTML holds `__NEXT_DATA__` (the command reads it, step 2.5). The captures hold real people's names and Jobright profile data, so keep them under `data/test/captures/` (gitignored), never in `tests/fixtures/`. Playwright can also record: `npx playwright open --save-har=example.har --save-har-glob=...` ([Playwright: Mock APIs, Recording a HAR file](https://playwright.dev/docs/mock)); it opens its own browser, so the user signs in there.
2. **Mutate.** Claude copies the capture and removes what the command treats as a changed layout: the Applied list, the job fields in `__NEXT_DATA__`, or the Insider Connection block (`.claude/commands/outreach-run.md`, "Jobright's page changed" row).
3. **Replay.** Two routes:
   - in the user's Chrome, DevTools Local Overrides serving the mutated document for the real jobright.ai URL (same tab caveat as above);
   - Playwright's `routeFromHAR` serves a HAR "instead of hitting the API", and the HAR JSON can be edited before replay ([Playwright: Mock APIs](https://playwright.dev/docs/mock)). This drives a Playwright browser, not Claude in Chrome, so it tests a parser or a scripted check, not the Run command's judgement **(inference)**.

For the Run command itself the override route is the only one that keeps the real command, the real extension and the real URL. Anything else (a local server on `localhost` with a test prompt) tests Claude's judgement on a modified command, not the command.

### Gmail connector not connected (real)

How to reproduce: the user disconnects the Gmail connector at [claude.ai/customize/connectors](https://claude.ai/customize/connectors), then starts a practice run. Step 1 reads bounces through the connector and halts with "Gmail connector not connected" when it "reports that authentication has expired or is not connected" (`.claude/commands/outreach-run.md`, step 1). Afterwards the user reconnects it there; that is an OAuth sign-in, so it is the user's step.

What Anthropic documents: Claude Code fetches claude.ai connectors at start when signed in with a claude.ai subscription; connectors "you can connect only from claude.ai settings"; a connector that rejects a credential mid-session gives `MCP server "<name>" needs you to sign in again (run /mcp to re-authenticate)` ([Connect Claude Code to tools via MCP, Use MCP servers from claude.ai](https://code.claude.com/docs/en/mcp#use-mcp-servers-from-claude-ai); [Error reference, MCP server needs you to sign in again](https://code.claude.com/docs/en/errors#mcp-server-needs-you-to-sign-in-again)). The docs do not say whether a connector disconnected in claude.ai before the session starts is listed but failing, or absent from the tool list. In the second case Claude sees no Gmail tools at all rather than an "expired" error; the test shows whether the command's wording ("not connected") still makes Claude halt.

A milder variant with no claude.ai change: `/mcp` in the session can disable a claude.ai connector per project, which Claude Code writes to `disabledMcpServers` ([MCP docs](https://code.claude.com/docs/en/mcp)). That is a project setting change, so the user does it, and undoes it after.

### Gmail sign-in expired or revoked, Python token (real, on a copy)

What the code does: when the saved access token is within 60 seconds of `expires_at`, `_access_token` posts the refresh token to `https://oauth2.googleapis.com/token`. A 401 or an `invalid_grant` answer raises `GmailAuthError("Gmail authorization has expired or was revoked (invalid_grant); run `outreach gmail-auth` to sign in to Gmail again")`; any other 4xx is a plain `GmailError("Google sign-in failed: ...")` (`_post_form` in `src/outreach/gmail_api.py`). A 401 or `authError` from Gmail itself raises `GmailAuthError("Gmail refused the saved authorization ...")` (`_http_error`). Both carry the text the command's "Gmail sign-in expired" row looks for. `tests/test_gmail_api.py` checks the `invalid_grant` mapping against a local server.

What Google documents: `invalid_grant` on refresh means "the token may have expired or has been invalidated", fixed by authenticating again ([OAuth 2.0 for iOS & Desktop Apps, Errors](https://developers.google.com/identity/protocols/oauth2/native-app)). The OAuth spec defines `invalid_grant` as a grant or refresh token that "is invalid, expired, revoked ... or was issued to another client" ([RFC 6749 §5.2](https://datatracker.ietf.org/doc/html/rfc6749#section-5.2)).

Safe real test, on a copy (Claude can do all of it; no settings change, no real file touched):

1. Copy `data/gmail_token.json` to `data/test/gmail_token.copy.json` (mode 600), and write `data/test/outreach.test.toml` with `gmail_token = "data/test/gmail_token.copy.json"` plus the real file's other settings.
2. In the copy, set `expires_at` to `0` (forces the refresh) and replace `refresh_token` with a garbage string.
3. Run a read-only call: `uv run python -c "from pathlib import Path; from outreach.config import load_config; from outreach.gmail_api import RealGmail; print(RealGmail(load_config(Path('data/test/outreach.test.toml'))).profile_address())"` (`users.getProfile`, within `gmail.modify`).
4. Expect `GmailAuthError: Gmail authorization has expired or was revoked (invalid_grant)`. That Google answers a garbage refresh token with `invalid_grant` rather than another code follows from RFC 6749's "invalid" but is not stated by Google for this case **(inference; this step verifies it)**.

Why not revoke for real on a second token: Google says revocation "removes all OAuth 2.0 scopes previously granted to a project, invalidating any issued access or refresh tokens for all clients registered under that project" ([OAuth 2.0 for iOS & Desktop Apps, Revoking a token](https://developers.google.com/identity/protocols/oauth2/native-app)). Revoking any token, or removing the app at the Google Account linked-apps page ([Google Account Help: Manage connections between your Google Account and third parties](https://support.google.com/accounts/answer/13533235), which gives `https://myaccount.google.com/linkedapps`), also kills the real token. Do it only when the user accepts rerunning `outreach gmail-auth` afterwards, and only the user does it. Google adds that "it might take some time before the revocation has full effect" (same page), and the cached access token in the file is used until it expires (`_access_token`), so after a real revoke the first error may come from Gmail (`Gmail refused the saved authorization`) rather than from the refresh. Set `expires_at` to `0` in the copy to test the refresh path.

Other real causes, for reference: a password change revokes tokens with Gmail scopes, and six months unused expires one ([docs/research/gmail-oauth-production-status.md](gmail-oauth-production-status.md)).

Within a Run: because a dry run never calls the adapter (finding 1), the halt for this failure can only be seen in a non-dry Run. A non-dry `prepare` creates Gmail drafts but sends nothing until the review page is confirmed (`docs/review-handoff-files.md`). That is a defensible later step with `test_recipient` set (every draft goes to a plus-address of the user's own, same file), but it is outside "practice runs only", so the plan below stops at the adapter test.

### Gmail is not answering, Python (real, for one process)

Without touching system settings: Python's `urlopen` installs a `ProxyHandler` "if proxy settings are detected (for example, when a `*_proxy` environment variable ... is set)", and environment variables win over macOS System Configuration ([Python docs: urllib.request](https://docs.python.org/3/library/urllib.request.html)). Pointing `HTTPS_PROXY` at a closed local port therefore cuts this one process off. Checked on 2026-11-14 with no network traffic: `HTTPS_PROXY=http://127.0.0.1:9` on `_post_form` gave `GmailUncertain: cannot reach Google sign-in: <urlopen error [Errno 61] Connection refused>`, the text the "Gmail is not answering" row looks for. With a still-valid access token in the copy the same trick on step 3 above gives `no answer from Gmail: ...` (`_call`).

Do not put the variable on `outreach start`: Claude Code also honours `HTTPS_PROXY` ([Enterprise network configuration](https://code.claude.com/docs/en/network-config)), so the whole session would lose the API, not just Gmail.

`sandbox-exec` is the other per-process way, but its man page says "The sandbox-exec command is DEPRECATED" and points developers to App Sandbox (`man sandbox-exec`, macOS 26). Don't build tests on it.

Whole-Mac loss (the user turns Wi-Fi off) tests Claude Code, not the CLI: Claude Code "retries transient failures up to 10 times with exponential backoff before showing you an error" such as `Unable to connect to API` ([Error reference, Automatic retries](https://code.claude.com/docs/en/errors)). Claude cannot then run `outreach halt` (finding 2). The docs do not say whether the interactive session exits after that error; the session staying open at the prompt is the likely result **(inference)**. Worth one real try by the user mid-practice-run, to see what the terminal shows and that a resume works.

### Gmail sending limit (fakes only)

Google's numbers for a personal account: sending pauses after "more than 500 emails sent in a day" or one email to more than 500 recipients, and "you should be able to send emails again within 1 to 24 hours" ([Gmail Help: Limits for sending & getting mail](https://support.google.com/mail/answer/22839)). "The Gmail API enforces the standard daily mail sending limits" and reports `429` "User-rate limit exceeded (Mail sending)" ([Gmail API: Resolve errors](https://developers.google.com/workspace/gmail/api/guides/handle-errors)). The per-user API quota is 6,000 units a minute, and `drafts.send` costs 100 ([Gmail API: Usage limits](https://developers.google.com/workspace/gmail/api/reference/quota)).

Reaching it means sending 500 real emails and locking the user's own Gmail for up to a day. Never do it. The adapter maps `429` and the limit reasons to `GmailQuotaError("Gmail limit reached ...")` (`_http_error`, `_QUOTA_REASONS`), and the in-memory fake scripts failures (`tests/fake_gmail.py`, `gmail.fail(...)`). Keep this one in the fake tests, and if wanted add a local-server test like the `invalid_grant` one with Google's documented 429 body.

### A file was rejected (real)

Real and easy: the CLI's validation is the thing under test. Before the user starts a practice run, Claude creates the test Run folder `data/test/<scenario>/<today>/` and seeds a malformed handoff file, for example an `applications.json` whose `match_score` is a string. Existence means done, so Claude skips step 2 and runs `outreach shortlist`, which rejects the file (`.claude/commands/outreach-run.md`, "Resuming"; shapes in `docs/contact-handoff-files.md`). The command allows "one fix-and-retry"; a seeded file Claude can fix tests only that path, so also seed one it cannot fix from the data it has (for example a broken file whose real content would need re-reading Jobright, while the command forbids rewriting a complete file). Expect "A file was rejected" quoting the error. No Jobright pages are read when step 2 is skipped, which also makes this the cheapest test.

### Claude Code usage limit (observe only)

Documented behaviour: "Claude Code blocks further requests until the reset time" and shows `You've hit your session limit · resets 3:45pm` or the weekly / Opus / Sonnet variants ([Error reference, Usage limits](https://code.claude.com/docs/en/errors#youve-hit-your-session-limit)). In an interactive session with a claude.ai subscription, automatic continue is on by default: the session shows `Usage limit reached · limit resets ...` / `Continuing automatically at ... · Esc to cancel`, waits, and continues the task after the reset; after a sleep of more than about 30 minutes it asks for Enter instead; it stops after three automatic continuations; exiting cancels it ([Interactive mode, Wait for a usage limit to reset](https://code.claude.com/docs/en/interactive-mode#wait-for-a-usage-limit-to-reset)). Nothing says the process exits, so `outreach start` keeps waiting and `caffeinate` keeps the Mac awake through the wait (`docs/run-files.md`) **(inference from both)**. The `StopFailure` hook fires "when the turn ends due to an API error" with matcher `rate_limit` among others ([Hooks reference](https://code.claude.com/docs/en/hooks)), a possible later way for the Run to notice; not needed now.

It cannot be induced without spending the user's real allowance. Observe it if it happens, and check the Run resumes.

### Context exhaustion (observe only)

Documented behaviour: auto-compact is on by default and "normally prevents" the error; when it fails the session shows `Context limit reached · /compact or /clear to continue`, or `Prompt is too long · automatic compaction failed: <the underlying error>` ([Error reference, Prompt is too long](https://code.claude.com/docs/en/errors#prompt-is-too-long)). The page does not say the process exits; the session waits for the user **(inference)**. Compaction does not lose the Run's progress on disk, because the handoff files and `run_state.json` hold it (`docs/run-files.md`), but a compacted summary can drop details of the command's rules **(inference)**. A long real Run (many Company Groups and Contacts) is the only realistic trigger; there is no cheap safe way to force it. Observe and note it.

### The user quitting Claude (real)

The user presses Ctrl-C twice on an empty prompt or Ctrl-D twice within 800 ms ([Interactive mode, keyboard shortcuts](https://code.claude.com/docs/en/interactive-mode)), mid-practice-run, or runs `outreach stop` from another terminal. `start` marks the Run `ended` on exit code 0 and `failed` on any other, and `stop` marks it `stopped` (`start_run`, `stop_run` in `src/outreach/run.py`). Anthropic does not document Claude Code's exit code for a user quit. If it is 0, a Run quit half-way is recorded as `ended` with the event "Claude Code exited with 0" **(inference from the code)**; the test should read the Run's `events` rows in the test database and the menu title to see which happens, and check that `outreach start` resumes it.

## Fault injection in general

The Principles of Chaos Engineering describe the same loop used here: define a measurable steady state, hypothesise it holds, "introduce variables that reflect real world events", and look for a difference; prioritise events "by potential impact or estimated frequency"; and "minimize blast radius" ([principlesofchaos.org](https://principlesofchaos.org/)). Here the steady state is "the Run ends with a named problem, the menu shows it, and the next `outreach start` resumes", and the blast radius is kept small by test copies and `--dry-run`. The same page prefers experimenting in production; for a personal mailbox and a personal Jobright account this note deliberately does not. No Anthropic page found gives fault-injection guidance specific to agents.

## Real-world test plan

In order, cheapest and safest first. Each uses its own `--data-dir data/test/<n>` and `--db data/test/<n>.db` and `--config data/test/outreach.test.toml`, and every `outreach start` is `--dry-run`, typed by the user. After each, check the terminal line, `halted` in the test DB, the menu title, then fix and resume once.

1. **Claude sets up** `data/test/`: the test config and the token copy (step 1 of the Gmail sign-in section). Nothing real is written.
2. **Gmail adapter, auth** (no Run): Claude corrupts the copy and runs the read-only probe; expect `Gmail authorization has expired or was revoked (invalid_grant)`.
3. **Gmail adapter, no answer** (no Run): Claude reruns the probe with `HTTPS_PROXY=http://127.0.0.1:9`; expect `cannot reach Google sign-in` (expired copy) and `no answer from Gmail` (fresh copy).
4. **A file was rejected**: Claude seeds a bad `applications.json`; the user starts a practice run; expect the halt, no Jobright reads.
5. **Gmail connector**: the user disconnects it in claude.ai, starts a practice run; expect a halt at step 1; the user reconnects.
6. **Chrome not connected**: the user turns the extension off, starts a practice run (watch for the install prompt); then a second run with Chrome quit mid-step-2; the user restores both.
7. **Signed out of Jobright**: the user signs out, starts a practice run; expect a halt rather than a pause; the user signs in.
8. **Quitting**: the user quits Claude mid-practice-run; read the Run's events for the exit code and status; resume.
9. **Optional, user's choice**: Wi-Fi off mid-practice-run (to see Claude Code's own error, not a halt); DevTools Offline or Local Overrides on Claude's tab for "blocked" and "page changed" with mutated captures.

Not tested for real, by design: the Gmail sending limit (fakes), a CAPTCHA or rate limit (replay at most), a real Google revoke (only if the user accepts re-signing in), usage limits and context exhaustion (observe when they happen).

## Progress (checkpoint)

Checkpoint 2026-11-14. Paused to build before testing further.

| Test | Result |
|---|---|
| Stand-in Claude, 12 scripted scenarios through the real `outreach start` (quit, no applications, none applied, no addresses, review confirmed, 8 halts) | all pass; no stray processes |
| Resume after a halt and after `outreach stop` | pass |
| Gmail adapter against real Google, on token copies: healthy, corrupted (`invalid_grant`), `HTTPS_PROXY` to a dead port | pass; messages as expected, so the `invalid_grant` inference holds |
| Real Claude, practice run, no applications today | pass: Run ended by itself |
| Real Claude, broken Gmail token copy, non-dry Run | pass: halted "Gmail sign-in expired" |
| Real Claude, rejected file (`data/test/a`) | found a bug, fixed: Claude asked the user a question instead of halting. `outreach start` now passes `--disallowedTools=AskUserQuestion` (written with `=`: the variadic flag otherwise swallows the prompt). Rerun: halted by itself with "A file was rejected" |

Not yet done, in this order:

1. **Gmail connector** (set up in `data/test/b`: an empty applications list, no `bounces.json`). The user disconnects the Gmail connector at claude.ai/customize/connectors, opens a fresh terminal, runs `uv run outreach start --dry-run --data-dir data/test/b --db data/test/b.db --config data/test/outreach.test.toml`, then reconnects. Open question: does a disconnected connector fail or just vanish from Claude's tools, and does Claude still halt?
2. **Chrome not connected**: extension off at start, and Chrome quit mid-Run. Watch for the install prompt.
3. **Signed out of Jobright**: expect a halt, not a pause.
4. **Quitting**: read the exit code and status the Run records; resume.
5. **Optional**: Wi-Fi off mid-Run; DevTools Offline or Local Overrides for "blocked" and "page changed".

Open risks to look at during 2 and 3: Claude Code's own "allow this action?" prompts. The user's settings pre-approve only a few Chrome tools, so a real Jobright step may still prompt. `dontAsk` mode would deny instead, but could break a working Run, so it is untried.

The test folders `data/test/a` and `data/test/b` and `data/test/outreach.test.toml` are still in place (gitignored). Delete `data/test` when the testing is done.

## Sources

- Use Claude Code with Chrome: https://code.claude.com/docs/en/chrome
- Getting started with Claude in Chrome: https://support.claude.com/en/articles/12012173-getting-started-with-claude-in-chrome
- Claude Code error reference: https://code.claude.com/docs/en/errors
- Claude Code interactive mode: https://code.claude.com/docs/en/interactive-mode
- Claude Code MCP (claude.ai connectors): https://code.claude.com/docs/en/mcp
- Claude Code hooks reference: https://code.claude.com/docs/en/hooks
- Claude Code network configuration: https://code.claude.com/docs/en/network-config
- OAuth 2.0 for iOS & Desktop Apps (errors, revocation): https://developers.google.com/identity/protocols/oauth2/native-app
- RFC 6749 §5.2 (OAuth error codes): https://datatracker.ietf.org/doc/html/rfc6749#section-5.2
- Google Account Help, third-party connections: https://support.google.com/accounts/answer/13533235
- Gmail Help, sending limits: https://support.google.com/mail/answer/22839
- Gmail API, resolve errors: https://developers.google.com/workspace/gmail/api/guides/handle-errors
- Gmail API, usage limits: https://developers.google.com/workspace/gmail/api/reference/quota
- Python urllib.request (proxies): https://docs.python.org/3/library/urllib.request.html
- `man sandbox-exec` (macOS 26)
- Chrome DevTools Network features reference: https://developer.chrome.com/docs/devtools/network/reference
- Chrome DevTools local overrides: https://developer.chrome.com/docs/devtools/overrides
- Chrome Help, multiple profiles: https://support.google.com/chrome/answer/2364824
- Playwright, mock APIs and HAR: https://playwright.dev/docs/mock
- Principles of Chaos Engineering: https://principlesofchaos.org/
