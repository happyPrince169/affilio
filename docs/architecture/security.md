# Security design (M3A)

## Identity

- **Local accounts.** Users sign in with email + password. Passwords are hashed with
  argon2id (`argon2-cffi`, 64 MiB, t=3, p=2) in `affilio.core.security`; hashes are never
  returned or logged. A dummy hash is verified for unknown emails so timing does not reveal
  valid accounts. Minimum password length is 10.
- **Server-side sessions.** Login creates a `user_sessions` row holding only the SHA-256 of a
  256-bit random token. The raw token is set as the `affilio_session` cookie: `HttpOnly`,
  `SameSite=Lax`, `Path=/`, `Secure` outside development/test (`SESSION_COOKIE_SECURE`
  overrides for plain-HTTP LAN deployments). Sessions slide (`SESSION_TTL_DAYS`, default 14),
  can be revoked, and are revoked wholesale on password change (except the current one).
  Nothing identity-related is stored in `localStorage` or exposed to JavaScript.
- **Service identity.** `X-Affilio-Admin-Token` (constant-time compared with
  `MEDIA_ADMIN_TOKEN`) authenticates CLI/cron calls such as scan triggers and
  `release-due`. It acts as `owner` of `DEFAULT_ORGANIZATION_SLUG` (or the header-selected
  organization) and is reported as `identity_kind = service`. Browsers never hold it: the
  Next.js proxy does not inject it any more.

## Authorization

`affilio/api/deps.py` resolves an `Identity` for every request under `/api/v1` except health,
system info and login/logout. Resolution fails closed:

1. Session cookie → user (401 `authentication_required` if missing, expired, revoked,
   tampered, or the user is not active).
2. Organization = the one named by `X-Organization-Slug` **if the user is a member**, else the
   user's first membership; a non-member slug is 403 `forbidden` with the slug echoed.
3. Role from `organization_memberships`: `viewer < editor < admin < owner`.
4. Otherwise the service token, otherwise 401.

Routers declare the minimum role with `CurrentIdentity` (any role), `RequireEditor`
(create/edit/submit/schedule/start/confirm/fail/retry/cancel, product/procurement/production
writes) or `RequireAdmin` (approve, revoke approval, reconcile, account registry changes,
member management, purchase-request decisions, handoff review, scan triggers, audit log).
`GET /auth/me` returns the effective permissions so the UI hides what the API would refuse;
the API remains the enforcement point. Service functions take `organization_id` on every
query, so a valid identity still cannot read or write another tenant's rows (404).

## CSRF

Cookie-authenticated unsafe requests (anything but GET/HEAD/OPTIONS) must carry an `Origin`
that is either in `CORS_ORIGINS` or equal to the request host (`X-Forwarded-Host` when the
Next.js proxy is in front). A `Sec-Fetch-Site: cross-site` request is rejected the same way
(403 `csrf_rejected`). `SameSite=Lax` is the second layer. Reads are never blocked.

## Web proxy

`apps/web/src/app/api/[...path]/route.ts` forwards `cookie`, `origin`, `sec-fetch-site` and
`user-agent` to the API, sets `X-Forwarded-Host/Proto/For`, passes every `Set-Cookie` back,
and attaches no credentials of its own. An unauthenticated browser request therefore reaches
the API unauthenticated.

## Audit

`audit_events` records actor kind (`user` / `service` / `system`), actor user id and label,
action, entity type/id, structured data and the request id for logins, logouts, password
changes, member and organization changes, account registry changes, link changes and every
publication transition. Admins read it via `GET /auth/audit`.

## Secrets and tokens

- No platform tokens exist in M3A. `social_accounts` has no credential column; M3B will add a
  separate encrypted credential store, never plaintext on the account row.
- Settings that are secrets are `SecretStr`; `.env.example` holds placeholders only.
- Passwords are never accepted on the command line: `affilio.scripts.create_user` reads them
  from `AFFILIO_NEW_USER_PASSWORD` or an interactive prompt.

## Login rate limiting (pre-M3B hardening)

`POST /auth/login` counts **failed** attempts per client IP (the socket peer, or the first
`X-Forwarded-For` hop only when `TRUST_PROXY_HEADERS=true`, which the Synology compose sets
because the API has no published port) and per email in a sliding window
(`AUTH_LOGIN_MAX_ATTEMPTS_PER_MINUTE`, default 10, over `AUTH_LOGIN_WINDOW_SECONDS`,
default 60). Over the limit the endpoint answers 429 `too_many_attempts` with a
`Retry-After` header and performs no password verification. A successful sign-in clears the
email bucket. Failed attempts are audited (`auth.login_failed`, organization-less).
The limiter is in-process (`affilio.core.ratelimit`): the Synology deployment runs one
uvicorn worker, so the limit is exact there; with N workers it is N × limit. A shared store
is deliberately not introduced.

## Review notes (pre-M3B)

| Area | State | Note |
| --- | --- | --- |
| Session expiry | sliding 14 days, touched at most every 15 min | expired rows are not purged yet; add a worker sweep when the table grows |
| Revocation | logout revokes the session; password change revokes all others; suspended users fail resolution | no admin "sign out everywhere" endpoint yet (use `create_user --reset-password`) |
| Cookie | HttpOnly, SameSite=Lax, Path=/, Secure unless `SESSION_COOKIE_SECURE=false` | the Synology example sets `false` for plain-HTTP LAN; switch to unset once TLS terminates on the DSM reverse proxy |
| CSRF | Origin / Sec-Fetch-Site check on cookie writes + Lax | `X-Forwarded-Host` is trusted from the web proxy only because the API has no published port |
| Roles | enforced per router (`RequireEditor` / `RequireAdmin`), services scope by organization | viewer cannot mutate anything; owner is only assignable through the seed or SQL |
| Service token | acts as **owner** of one organization | broader than needed; M3B should narrow it to scan / release-due / reconciliation endpoints |
| Seed credentials | `seed_dev` refuses outside development/test; production users via `create_user` with password from env or prompt | `SEED_ADMIN_PASSWORD` default is documented as a local placeholder and never used in production |

## Known limitations

- No password reset flow; an admin resets with `create_user --reset-password`.
- Session cookies are host-scoped: in development the web app and the API must be reached
  through the same host name (`localhost:3000` and `localhost:8000`), or use the same-origin
  proxy (`NEXT_PUBLIC_API_URL=""`).
- Expired sessions are retained until a cleanup sweep exists.
