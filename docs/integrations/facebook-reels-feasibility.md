# Facebook Page Reels API — feasibility report (M3B.0)

- **Status:** Research complete, 2026-10-26. No Meta app exists yet; no token was created; no
  content was published. Findings come from official Meta pages fetched on the verification
  date (listed in §9), with verbatim quotes where the wording matters.
- **Current Graph API version on the docs:** v26.0 (released 2026-07-29; v25.0 released
  2026-02-18 and supported until 2028-07-29). Meta guarantees a version for at least two years.
- **Headline:** publishing a Reel to a Facebook Page through the official Graph API is
  **VERIFIED_SUPPORTED** for Pages the operator manages, needs no App Review when every
  connecting user holds a role on the Meta app (Standard Access), and provides upload,
  scheduling, status polling and lifetime insights. It provides **no idempotency key**, **no
  documented upload-session lifetime**, and only partially documented post-identity fields;
  the architecture (`facebook-reels-architecture.md`) is built around those gaps.

Classification legend: **VERIFIED_SUPPORTED** (documented and current), **VERIFIED_UNSUPPORTED**
(documented as not available), **REQUIRES_PERMISSION_OR_REVIEW** (works only with a listed
permission, access level or review), **UNCONFIRMED** (not found in official documentation on
the verification date; must not be relied on).

## 1. Capability matrix

### A. App, login, tokens, permissions, review, policy

| # | Capability | Classification | Evidence (verified 2026-10-26) |
| --- | --- | --- | --- |
| A1 | Create a Meta app for Page publishing: **Business** app type / "Manage everything on your Page" use case, connected to a business portfolio | VERIFIED_SUPPORTED | App types page: "App types cannot be changed." "Business apps do not have app modes and instead rely exclusively on access levels." Create-an-app page: the use case adds `business_management`, `pages_show_list`, `public_profile` as required; "If your app will access data that you don't own or manage, you must connect your app to a business portfolio." [S1][S2] |
| A2 | Authorization-code flow for the web: `https://www.facebook.com/v26.0/dialog/oauth?client_id&redirect_uri&state&scope`, exchange at `GET /oauth/access_token` server-to-server with `client_secret` | VERIFIED_SUPPORTED | Manual flow page (v26.0): `state` "should be used for preventing Cross-site Request Forgery"; "Never include this app secret in client-side code"; cancel returns `error=access_denied&error_reason=user_denied`. PKCE is not documented → not used. [S3] |
| A2a | Redirect URI allow-list, HTTPS, Strict Mode, app secret proof | VERIFIED_SUPPORTED (HTTPS and exact match are mandatory) | Security page: "The full URI must be an exact match, including all parameters"; "Enabling Strict Mode is required for all apps"; Enforce HTTPS "requires HTTPS for OAuth Redirects"; `appsecret_proof` = "sha256 hash of your access token, using your app secret as the key"; "Require App Secret" setting. Whether `localhost` is exempt from HTTPS for role-holders: UNCONFIRMED. [S4] |
| A2b | Facebook Login for Business (`config_id`, Business Integration System User tokens that "default to never expire") | VERIFIED_SUPPORTED, REQUIRES_PERMISSION_OR_REVIEW for third-party businesses | FLB page (v26.0): "Your Meta app must be a business type app"; "You will receive a Configuration ID"; BISU token "Defaults to never expire"; "To serve businesses that you do not own or manage, your app must be approved for Advanced Access." [S5] |
| A3 | Token lifetimes: short-lived user ≈ 1–2 h; long-lived user ≈ 60 d via `fb_exchange_token`; **Page tokens from a long-lived user token have no expiry** | VERIFIED_SUPPORTED | Access tokens page: "Short-lived tokens typically last about one to two hours, while long-lived tokens last about 60 days." "Do not depend on these lifetimes remaining the same". Long-lived page: "Long-lived Page access token do not have an expiration date and only expire or are invalidated under certain conditions." [S6][S7] |
| A3a | Token inspection `GET /debug_token?input_token=` (fields `app_id`, `expires_at`, `data_access_expires_at`, `is_valid`, `issued_at`, `scopes`, `granular_scopes`, `user_id`) | VERIFIED_SUPPORTED | Reference (v26.0). Requires an app access token or the developer's user token. [S8] |
| A3b | 90-day data-access expiry does **not** apply to the Page permissions used here | VERIFIED_SUPPORTED | Auth-vs-data page lists `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`, `read_insights`, `business_management` … as permissions that do not expire; separately, a permission unused for 90 days must be re-granted. [S9] |
| A4 | Permissions for discovery + Reels publishing: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`; Page `CREATE_CONTENT` task | REQUIRES_PERMISSION_OR_REVIEW (Standard Access for role-holders; Advanced Access otherwise) | Reels guide: Page token "from a user who can perform the `CREATE_CONTENT` task on the Page". Permissions reference (descriptions quoted in `meta-app-setup.md` §5). Access levels: "Permissions with Standard Access can only be requested from app users who have a role on the requesting app." [S10][S11][S12] |
| A4a | `read_insights` for Reels metrics | REQUIRES_PERMISSION_OR_REVIEW | Permissions reference: "allows your app to read the Insights data for Pages". The video_insights reference also names `pages_manage_engagement`; the insights guide names `pages_read_engagement` — exact set UNCONFIRMED until tested. [S11][S20] |
| A4b | `business_management` | REQUIRES_PERMISSION_OR_REVIEW; avoid under Advanced Access | Listed as required by the Page use case; its review screencast must show ads data. Whether the dashboard really forces it: UNCONFIRMED (unknown #4). [S2][S11] |
| A5 | App Review / Advanced Access / Business Verification | REQUIRES_PERMISSION_OR_REVIEW only for users without an app role | App Review page: for role-holders "App Review is not required". Access levels: "Business Verification is required to get Advanced Access." Submission needs per-permission silent 1080p screencasts, a test login, privacy policy URL, 1024×1024 icon, data deletion URL. [S12][S13][S14] |
| A5a | Data deletion requirement | VERIFIED_SUPPORTED (instructions URL **or** callback) | "Developers need to specify either a data deletion callback instruction URL or a callback URL." Callback is a `signed_request` POST (HMAC-SHA256 with the app secret) answered with `{url, confirmation_code}`. [S15] |
| A6 | Development vs Live mode | VERIFIED_SUPPORTED (not applicable to Business apps) | App modes page: dev mode "can only request permissions from role users"; Release page: "Business apps do not have app modes and rely exclusively on access levels." [S16][S17] |
| A7 | Revocation: `DELETE /{user-id}/permissions` (all) or `/{permission}`; re-request with `auth_type=rerequest`; `GET /{user-id}/permissions` → granted/declined/expired | VERIFIED_SUPPORTED | Request-revoke page: "any user access token for the person will be invalidated". [S18] |
| A7a | Error 190 and subcodes 458/459/460/463/464/467/492 | VERIFIED_SUPPORTED | Error-handling page: 190 "Access token has expired"; 460 "Password Changed"; 463/467 "expired, been revoked, or is otherwise invalid"; 492 "User associated with the Page access token does not have an appropriate role on the Page". [S19] |
| A7b | Deauthorize callback URL | VERIFIED_SUPPORTED (exists); payload UNCONFIRMED | "we allow them to provide a de-authorize callback URL which will be pinged whenever this occurs"; no payload documentation retrievable. [S21] |
| A8 | Storing access tokens server-side | VERIFIED_SUPPORTED with obligations | Platform Terms (2026-02-03) 6.a.i: "maintain administrative, physical, and technical safeguards"; 6.a.iv: "must protect and not transfer, share, or solicit Meta user IDs, access tokens, or app secrets". No explicit encryption-at-rest clause; encryption is how AFFILIO meets 6.a.i. [S22] |
| A8a | Publishing the same Reel to several Pages | VERIFIED_SUPPORTED with policy caveats | Reels guide: "developers can use the crossposting feature to publish a video on multiple pages". Developer Policies 1.4/1.7/5.6.2(b) (no spam, consent, no "very high frequencies"); Community Standards (Spam, Inauthentic Behaviour) treat repetitive high-frequency posting as a signal; error 506 "Duplicate posts cannot be published consecutively." [S10][S23][S24][S19] |
| A8b | Affiliate / branded-content disclosure on API-published Reels (`sponsor_id`, Paid Partnership label) | UNCONFIRMED | `sponsor_id` / `sponsor_relationship` exist on `/{page-id}/videos`; not documented on `video_reels`. Branded Content help pages are JS-only (not fetchable). [S25] |
| A9 | Versioning: each version lives ≥ 2 years; v26.0 current | VERIFIED_SUPPORTED | Versioning guide and changelog. v20.0 expired 2026-09-24; v25.0 until 2028-07-29. [S26] |

### B. Reels publishing, status, insights, webhooks, limits, errors

| # | Capability | Classification | Evidence (verified 2026-10-26) |
| --- | --- | --- | --- |
| B1 | Three-phase publish: `POST /{page-id}/video_reels upload_phase=start` → `{video_id, upload_url}`; binary upload; `POST /{page-id}/video_reels upload_phase=finish video_id video_state=PUBLISHED description=…` → `{success: true}` | VERIFIED_SUPPORTED, REQUIRES_PERMISSION_OR_REVIEW (A4) | Reels guide (v26.0 examples). Finish parameters documented: `video_id` (required), `upload_phase` (required; the table says "should be `complete`" while every example and the reference enum use `finish` — treat `finish` as canonical, test once), `video_state` ∈ {DRAFT, SCHEDULED, PUBLISHED} (required), `description` ("Supports hashtags"), `title`, `scheduled_publish_time` (Unix int), `is_ai_generated` (bool), `place`. Reference adds targeting params (`feed_targeting`, `targeting`, `age_min` …). [S10][S27] |
| B1a | Finish response carries `post_id` | UNCONFIRMED | Reference return struct declares `{video_id, upload_url, success, message, post_id}`; guide example shows only `{"success": true}`. [S27] |
| B2 | Binary upload to `https://rupload.facebook.com/video-upload/v26.0/{video_id}` with headers `Authorization: OAuth {token}`, `offset`, `file_size` (local file as `application/octet-stream` body) **or** `file_url` (public http(s) URL) | VERIFIED_SUPPORTED | Guide header table. `file_url` is unusable for AFFILIO (NAS has no public URL; Meta CDN URLs rejected; robots.txt must allow `facebookexternalhit/1.1`). [S10] |
| B2a | Resuming an interrupted upload | VERIFIED_SUPPORTED | "If the video upload is interrupted, it can be resumed." Resume = new POST with `offset` set to `uploading_phase.bytes_transfered` from `GET /{video_id}?fields=status`; restart = `offset=0`. Multi-chunk sequencing on rupload: UNCONFIRMED (guide shows one POST). [S10] |
| B2b | Legacy chunked `/{page-id}/videos` (`start/transfer/finish`) creating a Reel | VERIFIED_UNSUPPORTED | `/videos` has no Reel parameter; the guide treats Reels as a separate product. [S28] |
| B2c | Upload URL / session lifetime | UNCONFIRMED | Only `video_status=expired` ("must be uploaded again") and subcode 1363041 "Upload session ID has expired" are documented; no duration. [S10][S29] |
| B2d | File size bound for Reels | UNCONFIRMED (generic video bound 2 GB) | Reels page: "File size and video bitrate: not specified"; Video API errors: 1363023 "file over 2 GB". [S10][S29] |
| B3 | Video specification | VERIFIED_SUPPORTED | Verbatim: ".mp4 (recommended)"; "9 x 16"; "1080 x 1920 pixels (recommended). Minimum is 540 x 960 pixels"; "24 to 60 frames per second"; "3 to 90 seconds."; "Chroma subsampling 4:2:0"; "Closed GOP (2-5 seconds)"; "Compression – H.264, H.265 (VP9, AV1 are also supported)"; "Fixed frame rate"; "Progressive scan"; audio "AAC Low Complexity", "48kHz", "Stereo", "128kbs+". HEVC 10-bit: UNCONFIRMED (no bit-depth statement) → never upload the NAS proxies. Rejection subcodes 1363040 (aspect), 1363127 (resolution), 1363128 (duration), 1363129 (frame rate). [S10][S29] |
| B4 | Processing status `GET /{video_id}?fields=status` | VERIFIED_SUPPORTED | `video_status` ∈ {error, expired, processing, ready, uploading, upload_failed, upload_complete}; `uploading_phase{status, bytes_transfered, errors, source_file_size}`; `processing_phase{status, error}`; `publishing_phase{status, publish_status ∈ {draft, error, published, scheduled}, publish_time, error}`; phase `status` ∈ {completed, error, not_started, in_progress} (examples also print "complete"). `copyright_check_information{status, matches_found, copyright_matches[]}` requires `pages_read_engagement`. [S10] |
| B5 | Post identity after publishing: `permalink_url`, `post_id`, `published` on the Video node | UNCONFIRMED | Only a stripped Video reference was retrievable (13 legacy fields). `permalink_url` is verified on the Page Post node, which "does not return Reels". Listing via `GET /{page-id}/video_reels` is documented in the guide but the reference says reading is not available. Must be settled on a real account (unknown #1/#2). [S30][S31] |
| B6 | Caption = `description` (hashtags, emoji); `title` | VERIFIED_SUPPORTED | Guide + reference. Length limit, @-mentions and whether links are clickable: UNCONFIRMED. [S10][S27] |
| B7 | Scheduled publishing: `video_state=SCHEDULED` + `scheduled_publish_time` | VERIFIED_SUPPORTED | "the publish time must be greater than 10 minutes from the current time and within 29 days of the current date". Cancel/reschedule via API: UNCONFIRMED (`video_reels` has no update/delete; Page Post `DELETE` is "only select developers"). [S10][S27][S31] |
| B8 | Reels insights `GET /{video_id}/video_insights?metric=…` (`period=lifetime`) | VERIFIED_SUPPORTED, REQUIRES_PERMISSION_OR_REVIEW (`read_insights`, ANALYZE task) | Metrics listed under "Reels Metrics": `blue_reels_play_count`, `fb_reels_replay_count`, `fb_reels_total_plays`, `post_impressions_unique` (**at risk**: announced for deprecation with v26.0, still listed), `post_video_avg_time_watched`, `post_video_followers`, `post_video_likes_by_reaction_type`, `post_video_retention_graph`, `post_video_social_actions`, `post_video_view_time`. "Data is only available for the past 2 years." "A crossposted Video will have a unique video ID for each Page". Impressions → views replacement from 2025-11-15. [S20][S32][S33] |
| B9 | Webhooks for publish completion | VERIFIED_SUPPORTED only as a hint; REQUIRES_PERMISSION_OR_REVIEW (`pages_manage_metadata`) | Page webhook field `videos` "Describes changes to the encoding status of a video on a page"; `feed` covers posts. No Reel-specific or "scheduled post went live" event is documented → polling is the reliable mechanism, and webhooks would need public inbound HTTPS. [S34] |
| B10 | Rate limits | VERIFIED_SUPPORTED | Page-level "Calls within 24 hours = 4800 * Number of Engaged Users"; app-level "Calls within one hour = 200 * Number of Users"; headers `X-App-Usage`, `X-Business-Use-Case-Usage` (with `estimated_time_to_regain_access`); throttle codes 4, 17, 32, 613, 80001. **Reels quota: "Reels API is limited to 30 API-published posts within a 24-hour moving period."** [S35][S10] |
| B11 | Error envelope and codes | VERIFIED_SUPPORTED | `{error: {message, type, code, error_subcode, error_user_title, error_user_msg, fbtrace_id}}`; `is_transient` (Marketing API docs: "Wait, then retry"). video_reels table: 100, 104, 190, 200, 368, 613, 6000 "There was a problem uploading your video file. Please try again with another file." Video subcodes: 1363019/21/41 generic upload (retry), 1363030 timeout, 1363037 invalid offset, 1363033 partial file, 1363045 size mismatch, 1363023 >2 GB, 1363041 session expired; rupload text errors `OffsetInvalidError`, `PartialRequestError`, `ProcessingFailedError`. [S19][S29][S27] |
| B12 | Idempotency key for start/finish | VERIFIED_UNSUPPORTED | No Meta page mentions an idempotency key; the only related signal is error 506 "Duplicate post → Change the post content and retry". Duplicate prevention is entirely client-side. [S19][S10] |
| B13 | Finish with `video_state=PUBLISHED` is synchronous | UNCONFIRMED (treat as asynchronous) | Finish returns `{"success": true}`; a separate `publishing_phase` with `publish_status` and `publish_time` exists and `video_status` may still be `processing`. [S10] |
| B14 | Crossposting one upload to several Pages | VERIFIED_SUPPORTED for videos; Reels UNCONFIRMED | Crossposting guide (stale v7.0 examples): `allow_crossposting_for_pages`, `POST /{page-id}/videos?crossposted_video_id=`; Reels guide only offers the collaborator flow ("10 collaborator invitation per Page per 24 hours"). Not used: each Page needs its own caption/links/schedule anyway. [S36][S10] |

## 2. Required permissions and approval requirements (summary)

| Need | Permissions / settings | Review |
| --- | --- | --- |
| Discover Pages, publish Reels (M3B.1) | `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`; user holds `CREATE_CONTENT` on the Page; `appsecret_proof` on every call | None under Standard Access (connecting users must be app Admin/Developer/Tester). Advanced Access = App Review per permission + Business Verification + annual Data Use Checkup |
| Reels insights (M3C) | `read_insights` (+ possibly `pages_read_engagement`/`pages_manage_engagement`, ANALYZE task) | same as above |
| Webhooks (not planned) | `pages_manage_metadata`, public HTTPS endpoint | same |
| App dashboard | Business app + business portfolio, privacy policy URL, data deletion instructions URL or callback, Strict Mode, Enforce HTTPS, exact redirect URI | — |

## 3. Consequences for AFFILIO's design

1. **Standard Access is enough** for the operator's own Pages; the plan avoids App Review by
   making AFFILIO admins app testers. Advanced Access is a product decision for later.
2. **Page tokens do not expire** but can be invalidated at any time; daily validation and a
   190-error handler are mandatory; the long-lived user token (60 days) is only needed to
   re-list Pages.
3. **No idempotency key** → persist `video_id` before upload and never send `finish` twice;
   reconcile by reading the video status (architecture §3.3/§4).
4. **Finish is asynchronous** → poll `status` until `publishing_phase.publish_status=published`;
   the Page permalink must be fetched from the video (field set to be confirmed on a real
   account) or the UI will show the video id and the Page URL until then.
5. **Upload is resumable** by `offset`; AFFILIO pins file size/hash and resumes from
   `bytes_transfered`; session lifetime is unknown, so uploads start just-in-time and an
   `expired` status triggers a new session (safe: nothing published).
6. **30 API-published Reels per Page per 24 h** and the per-account minimum gap bound the
   queue; AFFILIO enforces the quota before calling Meta.
7. **Scheduling via Meta** (10 min to 29 days) is possible but cancel/reschedule via API is
   unconfirmed → AFFILIO keeps its own scheduler in M3B.1; Meta-side scheduling is an opt-in
   for M3B.2 once cancellation is confirmed.
8. **Video validation** is fact-based from the stored probe: MP4, 9:16, ≥ 540×960, 24–60 fps,
   3–90 s, H.264/H.265 with AAC 48 kHz stereo; HEVC 10-bit and > 2 GB are refused as unverified.
9. **Links**: clickable links in Reel descriptions are unconfirmed; the capability stays
   `clickable_links_in_caption=False`, so the affiliate links are shown in the caption text
   only when the operator chooses, and the "links in first comment" feature is deferred (it
   needs `pages_manage_engagement`).
10. **Policy**: per-Page distinct captions, enforced gaps, approval snapshots and audit cover
    the consent and spam rules; disclosure wording must be in the caption until the branded
    content question (A8b) is settled.

## 4. Unknowns requiring a real Meta developer account

Sorted by impact on M3B.1.

1. Whether the finish response or `GET /{video_id}?fields=post_id,permalink_url` yields the
   Page post id and permalink for a Reel (B1a/B5). Fallback: store `video_id` as
   `external_post_id` and derive the URL as `https://www.facebook.com/reel/{video_id}` only
   after confirming it resolves.
2. Upload URL / session lifetime and the Reels file-size bound (B2c/B2d).
3. Exact finish semantics (B13) and the `complete` vs `finish` value of `upload_phase`.
4. Whether `localhost` redirect URIs work for role-holders without HTTPS (A2a) — decides
   whether local development needs a TLS tunnel.
5. Which permissions the dashboard forces for the "Manage everything on your Page" use case
   (`business_management`?) and whether Facebook Login vs Facebook Login for Business is
   offered (A1/A2b/A4b).
6. Caption length limit, mention syntax and link clickability (B6).
7. Deauthorize callback payload (A7b).
8. Branded-content / Paid Partnership labelling for API-published Reels (A8b).
9. Reels insights permission set and the fate of `post_impressions_unique` (A4a/B8).
10. Cancel/reschedule of a Meta-scheduled Reel (B7).
11. HEVC 10-bit acceptance and multi-chunk rupload behaviour (B3/B2a).
12. Actual `expires_at` reported by `/debug_token` for a Page token derived from a long-lived
    user token, and for an FLB BISU token (A3/A2b).

## 5. Sources (fetched 2026-10-26)

- [S1] https://developers.facebook.com/docs/development/create-an-app/app-dashboard/app-types/
- [S2] https://developers.facebook.com/docs/development/create-an-app/
- [S3] https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow (v26.0)
- [S4] https://developers.facebook.com/docs/facebook-login/security
- [S5] https://developers.facebook.com/docs/facebook-login/facebook-login-for-business (v26.0)
- [S6] https://developers.facebook.com/docs/facebook-login/guides/access-tokens
- [S7] https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived (v26.0)
- [S8] https://developers.facebook.com/docs/graph-api/reference/debug_token/ (v26.0)
- [S9] https://developers.facebook.com/docs/facebook-login/auth-vs-data
- [S10] https://developers.facebook.com/docs/video-api/guides/reels-publishing (v26.0 examples; re-checked directly)
- [S11] https://developers.facebook.com/docs/permissions
- [S12] https://developers.facebook.com/docs/graph-api/overview/access-levels
- [S13] https://developers.facebook.com/docs/resp-plat-initiatives/individual-processes/app-review (+ /submission-guide, /content)
- [S14] https://developers.facebook.com/docs/development/release/business-verification
- [S15] https://developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback
- [S16] https://developers.facebook.com/docs/development/build-and-test/app-modes
- [S17] https://developers.facebook.com/docs/development/release/
- [S18] https://developers.facebook.com/docs/facebook-login/guides/permissions/request-revoke/ and /docs/graph-api/reference/user/permissions/ (v26.0)
- [S19] https://developers.facebook.com/docs/graph-api/guides/error-handling
- [S20] https://developers.facebook.com/docs/graph-api/reference/video/video_insights/ (v26.0)
- [S21] https://developers.facebook.com/documentation/facebook-login/web/accesstokens
- [S22] https://developers.facebook.com/terms/ (last updated 2026-02-03)
- [S23] https://developers.facebook.com/devpolicy/ (last updated 2026-02-03)
- [S24] https://transparency.meta.com/policies/community-standards/spam/ and …/inauthentic-behavior/
- [S25] https://developers.facebook.com/docs/graph-api/reference/page/videos/ (v26.0; `sponsor_id`)
- [S26] https://developers.facebook.com/docs/graph-api/guides/versioning and /docs/graph-api/changelog
- [S27] https://developers.facebook.com/docs/graph-api/reference/page/video_reels/ (v26.0)
- [S28] https://developers.facebook.com/docs/video-api/guides/publishing/ and /docs/graph-api/guides/upload/ (v26.0)
- [S29] https://developers.facebook.com/documentation/video-api/reference/error-codes
- [S30] https://developers.facebook.com/docs/graph-api/reference/video/ (stripped rendering only)
- [S31] https://developers.facebook.com/docs/graph-api/reference/page-post/ (v26.0)
- [S32] https://developers.facebook.com/docs/graph-api/changelog/version25.0 and /version26.0
- [S33] https://developers.facebook.com/blog/post/2025/08/15/page-insights-api-updates/
- [S34] https://developers.facebook.com/docs/graph-api/webhooks/reference/page/ (v26.0)
- [S35] https://developers.facebook.com/docs/graph-api/overview/rate-limiting/
- [S36] https://developers.facebook.com/docs/video-api/guides/crossposting/ (v7.0 examples)

Not retrievable on the verification date (404 or JavaScript-only shells): the modern Video
node reference, `/docs/video-api/guides/reels-insights`, the Business Help Center branded
content pages, the deauthorize callback guide, the use-cases dashboard page.
