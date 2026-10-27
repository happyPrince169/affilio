# Meta app setup for AFFILIO (Facebook Page Reels)

- **Status:** Operator guide, written from official Meta documentation verified on 2026-10-26
  (Graph API v26.0 current). Nothing here has been executed against a real Meta account yet;
  the "Unknowns" section in `facebook-reels-feasibility.md` lists what only a real account can
  confirm.
- **Who does this:** the person who owns the Facebook Pages (or an admin of the business
  portfolio that owns them). AFFILIO engineers never need the Meta password, the app secret is
  pasted into the NAS `.env` once.
- **Outcome:** a Meta app whose App ID / App Secret / redirect URI are configured in AFFILIO,
  and whose role-holders can authorize AFFILIO to publish Reels on the Pages they manage.

Sources: app types and use cases
(https://developers.facebook.com/docs/development/create-an-app/), access levels
(https://developers.facebook.com/docs/graph-api/overview/access-levels), Facebook Login security
(https://developers.facebook.com/docs/facebook-login/security), manual login flow
(https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow), permissions
reference (https://developers.facebook.com/docs/permissions), Reels publishing
(https://developers.facebook.com/docs/video-api/guides/reels-publishing), data deletion callback
(https://developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback).

## 1. Decide the access model first

Two models exist; the choice drives everything else.

| Model | Who can connect Pages | App Review | Business Verification | Fits AFFILIO today? |
| --- | --- | --- | --- | --- |
| **Standard Access** (role-holders only) | People with a role on the app (Administrator, Developer, Tester) who also have a role on the Page | No | No | **Yes.** The operator owns the Pages and the handful of AFFILIO admins can be app testers |
| **Advanced Access** (anyone) | Any Facebook user | Yes, per permission, with screencasts and a test login | Yes, plus annual Data Use Checkup | Only if AFFILIO is offered to third parties later |

Meta's words: "Permissions with Standard Access can only be requested from app users who have a
role on the requesting app" and "Business Verification is required to get Advanced Access."
Business-type apps "do not have app modes and instead rely exclusively on access levels", so
there is no Development/Live switch to flip for this app type.

Plan M3B.1 on Standard Access. Everything below assumes it; §8 lists what changes for
Advanced Access.

## 2. Prerequisites

1. A Facebook account that is an admin of a **business portfolio** (Meta Business Suite) which
   owns, or has agency access to, every Page that will publish. Meta: "If your app will access
   data that you don't own or manage, you must connect your app to a business portfolio."
2. The person doing the setup must be able to perform the `CREATE_CONTENT` task on each Page
   (Page access → Tasks). Pages where the user lacks it are listed by the API but cannot publish.
3. A **Meta developer account** (https://developers.facebook.com, "Get started", accept the
   developer terms, verify by phone or email).
4. An HTTPS hostname for AFFILIO. Meta enforces HTTPS and *Strict Mode* (exact match) on
   redirect URIs. For the Synology deployment that means the DSM reverse proxy with a
   certificate (Synology DDNS + Let's Encrypt, or a corporate certificate) in front of the web
   container; see `docs/deployment/synology.md` once M3B.1 updates it. The hostname must be
   reachable by the operator's browser; Meta's servers never call the redirect URI.
5. A public **privacy policy URL** and either a **data deletion instructions URL** or a data
   deletion callback. Meta: "Developers need to specify either a data deletion callback
   instruction URL or a callback URL." A static page on any public host is acceptable for
   the instructions variant and avoids exposing the NAS.

## 3. Create the app (App Dashboard)

1. https://developers.facebook.com/apps → **Create App**.
2. Use case: **"Manage everything on your Page"** (adds `pages_show_list`, `public_profile`,
   `business_management` as required and `pages_manage_engagement` by default). If the
   dashboard offers it as a Business-type app without that use case, choose **Other** →
   **Business**. Record which path the dashboard actually offered: the documentation says the
   `business_management` requirement comes from this use case (feasibility unknown #4).
3. Name it (for example `AFFILIO Publisher`), set the contact email, and **connect it to the
   business portfolio** that owns the Pages.
4. Settings → Basic: note the **App ID** and **App Secret** (Show → re-enter password). Fill
   in Privacy Policy URL, App icon (1024×1024), Category (Business and Pages), and the Data
   Deletion field (instructions URL or callback URL). Save.
5. Settings → Advanced → Security: turn **Require App Secret** on. AFFILIO sends
   `appsecret_proof` on every call, so this costs nothing and blocks any leaked token from
   being used elsewhere.

## 4. Configure Facebook Login

1. Add the **Facebook Login** product (or, if the dashboard shows **Facebook Login for
   Business** as the only option for this use case, add that; it issues a *Configuration ID*
   that replaces the `scope` parameter — AFFILIO supports both via `META_LOGIN_CONFIG_ID`).
2. Facebook Login → Settings:
   - **Client OAuth login**: Yes. **Web OAuth login**: Yes.
   - **Enforce HTTPS**: Yes (cannot be disabled for live apps). **Use Strict Mode for
     redirect URIs**: Yes ("Enabling Strict Mode is required for all apps").
   - **Embedded browser OAuth login**: No. **Login from devices**: No.
   - **Valid OAuth Redirect URIs**: exactly
     `https://<affilio-host>/api/v1/accounts/connect/facebook/callback` — one entry per
     environment (for example a staging host). No trailing slash differences, no port unless
     the browser really uses one; "The full URI must be an exact match, including all
     parameters".
   - **Deauthorize callback URL** (optional for M3B.1): only if the NAS path is publicly
     reachable; otherwise leave empty — AFFILIO detects revoked tokens by daily validation.
3. If Facebook Login for Business was added: create a **configuration** with login variation
   "User access token" (not "System-user access token" for M3B.1), permissions as in §5, and
   note the **Configuration ID**.

## 5. Permissions to request

Standard Access to these is automatic for role-holders; nothing to apply for.

| Permission | Why AFFILIO needs it (official description) | Needed in |
| --- | --- | --- |
| `pages_show_list` | "access the list of Pages a person manages" — Page discovery | M3B.1 |
| `pages_read_engagement` | "read content … posted by the Page" and Page metadata; dependency of `pages_manage_posts` | M3B.1 |
| `pages_manage_posts` | "create, edit and delete your Page posts" — required by `/{page-id}/video_reels` | M3B.1 |
| `business_management` | added as required by the "Manage everything on your Page" use case; AFFILIO does not call the Business Manager API | only if the dashboard forces it |
| `read_insights` | "read the Insights data for Pages" — Reels metrics | M3C (analytics), not M3B.1 |
| `pages_manage_engagement` | comments; added by default by the use case | remove unless the "links in first comment" feature is built |
| `pages_read_user_content` | user-generated content on the Page | not needed |
| `pages_manage_metadata` | Page webhooks and settings | not needed (polling design) |

The exact `scope` string AFFILIO sends is a setting (`META_OAUTH_SCOPES`), default
`pages_show_list,pages_read_engagement,pages_manage_posts`.

## 6. Add the people who will connect Pages

App Dashboard → **App Roles** → **Roles** → add each AFFILIO admin as **Tester** (or
Developer). They must accept the invitation at https://developers.facebook.com/requests.
Only these people can complete the "Connect Facebook Page" flow in AFFILIO under Standard
Access; anyone else gets a Meta error at the consent screen. Keep the list short and in the
audit trail (AFFILIO records who connected each Page).

## 7. Configure AFFILIO

In the Synology `.env` (mode 600) — placeholders, never real values in the repository:

```
META_APP_ID=
META_APP_SECRET=
META_GRAPH_API_VERSION=v26.0
META_OAUTH_REDIRECT_URI=https://<affilio-host>/api/v1/accounts/connect/facebook/callback
META_OAUTH_SCOPES=pages_show_list,pages_read_engagement,pages_manage_posts
META_LOGIN_CONFIG_ID=            # only for Facebook Login for Business configurations
CREDENTIAL_ENCRYPTION_KEYS=v1:<base64 32 bytes from `openssl rand -base64 32`>
CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID=v1
```

Then restart the stack. `/api/v1/system/info` reports only `integrations.meta.configured:
true/false` and the pinned API version — never the values. The accounts page shows "Connect
Facebook Page" only when configured.

Pin `META_GRAPH_API_VERSION` to a version with at least 18 months of life left (v25.0 is
supported until 2028-07-29; v26.0's end date is not yet announced). Unversioned calls are
never made.

## 8. Verification checklist (test Page first)

1. Create a **test Page** owned by the same portfolio (or use an existing low-traffic Page).
2. In AFFILIO (as an admin who is an app Tester): Accounts → Connect Facebook Page → Meta
   consent → select the test Page → the row shows **Connected** with the Page id and tasks.
3. Settings → Accounts → the Page detail shows token issued time, no expiry for the Page
   token, and the granted scopes; **no token value is visible anywhere**.
4. Publish one approved final as a Reel to the test Page with a short caption; confirm the
   permalink in AFFILIO matches the Page; confirm the publication is `api_verified`.
5. Remove the app from the Meta user (Facebook → Settings → Apps and websites) and run the
   daily validation: the account must flip to **Disconnected (reconnect needed)** and queued
   API jobs must fail with `page_not_connected`, never publish.
6. Reconnect; the same `social_accounts` row is re-used (publications and routing intact).
7. Back up the database and the `.env`; restore on a scratch host with and without the
   encryption key to confirm the documented behaviour (`credential_undecryptable` → reconnect).

## 9. What changes for Advanced Access (not planned)

- App Review per permission (`pages_show_list`, `pages_manage_posts`, `pages_read_engagement`,
  and `read_insights` for analytics) with a silent 1080p screencast per permission showing the
  exact actions, a test login Meta's reviewers can use ("If we are unable to access your app to
  test it, your entire submission will be rejected"), and the privacy policy / data deletion
  URLs above.
- Business Verification of the operator's business (only a Business admin can complete it)
  and the annual Data Use Checkup.
- `business_management` review requires demonstrating ads-performance data, which a
  non-ads app cannot do honestly; avoid requesting it under Advanced Access.

## 10. Policy obligations to keep in view

- Platform Terms §6: "maintain administrative, physical, and technical safeguards" and "must
  protect and not transfer, share, or solicit Meta user IDs, access tokens, or app secrets"
  — satisfied by encrypted-at-rest storage, server-only use, and the no-logging rule.
- Developer Policies 1.7: "Obtain consent from people before publishing content or taking any
  other action on their behalf" — the consent is the OAuth grant by a Page manager; AFFILIO's
  approval snapshot records which human approved each publication.
- Spam / inauthentic behaviour (Community Standards): the same Reel on many Pages is not
  forbidden (the Reels API itself offers crossposting), but "very high frequencies" and
  "repetitive content" are enforcement signals. AFFILIO's per-account minimum gap,
  per-Page captions, and the 30-posts-per-24h API cap are the guard rails; do not raise them
  to drive volume.
- Affiliate disclosure: Meta's branded-content label applies to content featuring a business
  partner "in exchange for something of value"; whether a Reel published via the API can carry
  the Paid Partnership label is unconfirmed (feasibility unknown #2). Until confirmed, put the
  disclosure text in the caption itself and follow Vietnamese advertising-disclosure rules,
  which are outside Meta's documentation.
