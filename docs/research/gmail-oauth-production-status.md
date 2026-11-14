# Gmail OAuth app: "Testing" vs "In production"

Researched 2026-11-14 for issue #23. Question: does moving our Gmail OAuth app from "Testing" to "In production" end the 7-day refresh-token expiry, and what does that cost for a personal-use app with the single scope `https://www.googleapis.com/auth/gmail.modify`?

Every claim below links to a Google-owned page. Where Google's docs are silent, this note says so instead of filling the gap.

## Answer

**Yes.** For this app (External user type, one user, `gmail.modify`), publishing to "In production" without verification stops the 7-day expiry. `outreach gmail-auth` then needs rerunning only when the token is revoked or invalidated for one of the reasons under [What can still revoke the token](#what-can-still-revoke-the-token). The cost is an "unverified app" warning that you click through at each sign-in. The app also stays capped at 100 users, which doesn't matter for one person.

Why:

1. The 7-day expiry is a Testing-status rule. "Authorizations by a test user will expire seven days from the time of consent", and a refresh token from offline access "will also expire" ([Manage App Audience](https://support.google.com/cloud/answer/15549945)). The OAuth overview gives the same rule for "a Google Cloud Platform project with an OAuth consent screen configured for an external user type and a publishing status of 'Testing'" ([Using OAuth 2.0 to Access Google APIs](https://developers.google.com/identity/protocols/oauth2#expiration)). Neither page applies it to In production.
2. The only exception in Testing is an app that asks for nothing beyond name, email and profile ([Manage App Audience](https://support.google.com/cloud/answer/15549945)). `gmail.modify` is not on that list, so staying in Testing always means a weekly re-sign-in.
3. A personal-use app doesn't need verification: "If the app is for your personal use (fewer than 100 users)… you and your limited number of users can continue using the app without going through verification", and "users will be allowed to click through 'unverified app' warning screens during sign-in" ([When is verification not needed](https://support.google.com/cloud/answer/13464323)).

## How `gmail.modify` is classified

| Scope | Classification |
|---|---|
| `gmail.modify` (ours) | Restricted |
| `gmail.compose` | Restricted |
| `gmail.readonly` | Restricted |
| `gmail.send` | Sensitive |
| `gmail.labels` | Non-sensitive |

Source: [Choose Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes).

What "Restricted" means here:

- **Verification.** Restricted scopes "require restricted scope OAuth App Verification" ([Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)). The personal-use exception above waives it for fewer than 100 users ([When is verification not needed](https://support.google.com/cloud/answer/13464323)).
- **"Unverified app" screen.** An app asking for sensitive or restricted scopes before verification shows the unverified-app warning ([Manage App Audience](https://support.google.com/cloud/answer/15549945), [Unverified apps](https://support.google.com/cloud/answer/7454865)). Google's app-state overview calls a published, unverified app "Strongly discouraged" and doesn't show its name and logo on the consent screen ([OAuth app state overview](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview)). That advice is aimed at public apps. For personal use the exception above explicitly allows clicking through.
- **User cap.** Unverified apps get "100 new users in total, after the app presents the unverified app screen", and "the user cap applies over the entire lifetime of the project, and it cannot be reset or changed" ([Manage App Audience](https://support.google.com/cloud/answer/15549945)). One user is far inside it.
- **Security assessment.** It's required only as part of restricted-scope verification, for apps that "store restricted scope data on servers (or transmit)" ([Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)). A personal-use app skips verification, so the assessment never comes up.

## What can still revoke the token

Publishing removes the 7-day rule only. Google lists these other ways a refresh token stops working ([Using OAuth 2.0 to Access Google APIs](https://developers.google.com/identity/protocols/oauth2#expiration)):

- You revoke the app's access.
- The token goes unused for six months. A daily Run uses it, so this only matters after a long break.
- **You change your Google password. Because the token includes Gmail scopes, this revokes it.** Rerun `outreach gmail-auth` after any password change.
- The account exceeds its maximum number of live refresh tokens. The limit is "100 refresh tokens per Google Account per OAuth 2.0 client ID", and once it's reached, the oldest token stops working without warning. Each `outreach gmail-auth` run creates a new token, so this would take over 100 re-sign-ins on this one client. There's also an undisclosed limit across all clients ([same page](https://developers.google.com/identity/protocols/oauth2#expiration), [Native apps](https://developers.google.com/identity/protocols/oauth2/native-app)).
- The access was time-based and has run out.
- A Workspace admin restricts the scope's service. This doesn't apply to a personal Gmail account.

**Can Google disable an unverified personal-use app?** None of the pages read for this note says so. The personal-use exception says you "can continue using the app without going through verification" ([When is verification not needed](https://support.google.com/cloud/answer/13464323)). The only enforcement Google describes for unverified apps is the 100-user cap ([Unverified apps](https://support.google.com/cloud/answer/7454865)).

## Switching to production

The console moved the consent-screen settings under **Google Auth platform**, split into Branding, Audience and Data Access ([Configure the OAuth consent screen](https://developers.google.com/workspace/guides/configure-oauth-consent)). The publish steps follow [Google Ads API: Brand verification](https://developers.google.com/google-ads/api/docs/api-policy/brand-verification), which gives the same clicks for any Cloud project:

1. Open the [Google Cloud console](https://console.cloud.google.com/) and select the project that holds the Outreach OAuth client.
2. Go to **Menu > Google Auth platform > Audience**. The older path, **APIs & Services > OAuth consent screen**, then the **Audience** tab, gets to the same page.
3. Check that **User type** is **External**. A personal Gmail account has no Internal option.
4. Under **Publishing status** (currently **Testing**), click **Publish app**.
5. When the console asks **Push to Production?**, click **Confirm**.
6. Run `outreach gmail-auth` once. On the "Google hasn't verified this app" screen, choose to continue, then grant the Gmail permission.

Why step 6 is needed: the current token was issued while the app was in Testing, and Google states it expires seven days from consent ([Manage App Audience](https://support.google.com/cloud/answer/15549945)). No Google page says publishing extends a token that was already issued, so sign in once more to get a token issued under In production.

## Switching back to Testing

Google's docs don't describe the steps to switch back. The Audience page shows a **Back to testing** button while the app is In production. That's from using the console, not from a Google page, so check the label before relying on it.

1. Go to **Menu > Google Auth platform > Audience** ([Configure the OAuth consent screen](https://developers.google.com/workspace/guides/configure-oauth-consent)).
2. Under **Publishing status** (now **In production**), click **Back to testing** and confirm.
3. Under **Test users**, make sure your own address is listed, adding it with **Add users** if needed. In Testing, "only users explicitly added to the test user allowlist can access the app" ([OAuth app state overview](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview), [Configure the OAuth consent screen](https://developers.google.com/workspace/guides/configure-oauth-consent)).
4. Run `outreach gmail-auth`. The new token is a Testing token and expires in 7 days again ([Using OAuth 2.0](https://developers.google.com/identity/protocols/oauth2#expiration)).

What happens to existing tokens either way:

- **Testing → In production:** Google doesn't document it. Treat the old token as still expiring 7 days after its consent, and sign in again (step 6 above).
- **In production → Testing:** Google says only that after this change "your app will continue to function according to its current configuration (e.g., adhering to 'Testing' user caps…)". It says nothing specific about refresh tokens issued while In production ([Brand Approvals & Auto-Cancellations](https://support.google.com/cloud/answer/16868008)). Don't count on an In-production token lasting. Sign in again (step 4) so the token matches the status.
- Switching back also cancels any pending verification, because "Apps in 'Testing' are ineligible for verification" ([same page](https://support.google.com/cloud/answer/16868008)). We have none pending.

## Would a narrower scope change the answer?

Recorded for reference only; this note doesn't recommend a code change.

- **The 7-day expiry:** no. In Testing, only name, email and profile scopes avoid it ([Manage App Audience](https://support.google.com/cloud/answer/15549945)), and every Gmail scope is outside that set.
- **The classification:** `gmail.send` is Sensitive, not Restricted ([Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)), so a send-only app would avoid the security-assessment path if it ever went for verification. It would still show the unverified screen and the 100-user cap until verified ([Manage App Audience](https://support.google.com/cloud/answer/15549945)). It also couldn't make the other calls the adapter needs (search Sent, apply the "Outreach" label; see the scope note in `src/outreach/gmail_api.py`). For an unverified personal-use app, the outcome is the same.

## Sources

- Using OAuth 2.0 to Access Google APIs (refresh token expiration): https://developers.google.com/identity/protocols/oauth2#expiration
- OAuth 2.0 for iOS & Desktop Apps (refresh token limits): https://developers.google.com/identity/protocols/oauth2/native-app
- Manage App Audience: https://support.google.com/cloud/answer/15549945
- When is verification not needed: https://support.google.com/cloud/answer/13464323
- Unverified apps: https://support.google.com/cloud/answer/7454865
- OAuth app state overview: https://developers.google.com/identity/protocols/oauth2/production-readiness/overview
- Brand Approvals & Auto-Cancellations: https://support.google.com/cloud/answer/16868008
- Choose Gmail API scopes: https://developers.google.com/workspace/gmail/api/auth/scopes
- Configure the OAuth consent screen: https://developers.google.com/workspace/guides/configure-oauth-consent
- Google Ads API, Brand verification (publish steps): https://developers.google.com/google-ads/api/docs/api-policy/brand-verification
