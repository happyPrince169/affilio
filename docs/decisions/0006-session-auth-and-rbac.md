# ADR 0006 — Cookie sessions with local passwords, role hierarchy at the API

- **Status:** Accepted (M3A, 2026-10-26)
- **Context:** Every mutating endpoint was gated by a shared admin token injected by the web
  proxy, so any browser on the LAN was effectively an administrator and audit events could
  not name a person. M3A introduces approvals and publishing, which need accountable
  identities and least privilege. The deployment is a self-hosted LAN app with a handful of
  users and no identity provider.
- **Decision:**
  1. Local email + password accounts (argon2id) and opaque server-side sessions delivered as
     `HttpOnly; SameSite=Lax; Secure` cookies. No JWTs: revocation and password changes must
     take effect immediately and the token must never be readable by scripts.
  2. Identity resolution in one FastAPI dependency that fails closed; organization context is
     derived from memberships, never from an unauthenticated header.
  3. Roles `viewer < editor < admin < owner` enforced by `RequireEditor` / `RequireAdmin`
     dependencies on routers; services keep `organization_id` scoping.
  4. The admin token survives only as a *service identity* for CLI/cron; the Next.js proxy no
     longer injects it.
  5. Origin/Sec-Fetch-Site check on cookie-authenticated unsafe requests as CSRF defence.
- **Consequences:** every existing API test signs in; the seed creates a local owner with a
  development password from settings; a `create_user` script bootstraps production users;
  OIDC can be added later behind the same `Identity` abstraction. Login rate limiting and
  password reset are deferred (documented in `docs/architecture/security.md`).
