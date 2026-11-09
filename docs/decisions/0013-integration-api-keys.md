# ADR 0013 — Scoped integration API keys and the MeoScout product ingestion contract

- **Status:** Proposed (M3.2-B.2 P1, 2026-11-09); implemented locally, not deployed.
- **Context:** MeoScout (a separate repository: Shopee research, scoring, affiliate link
  collection, candidate approval) hands approved candidates to AFFILIO today by CSV export and
  upload. The cross-repository audit
  (`meoscout/docs/milestones/M3.2-B.2-affilio-direct-integration-design.md`) proposes a
  server-to-server handoff instead. AFFILIO has two credential kinds: browser sessions
  (ADR 0006) and the owner-level `X-Affilio-Admin-Token` service token, confined to scan
  triggers and `release-due` (M3B.1A). Neither fits a laptop- or NAS-side relay that must do
  exactly one thing: the admin token grants owner rights and cannot be revoked per
  integration; a session belongs to a person. The audit's NO-GO list rules out reusing the
  admin token, putting any AFFILIO credential in the browser extension, and any ingest
  behaviour that changes product status, buys, or publishes.
- **Decision:**
  1. **A third identity kind, `integration`,** backed by `integration_api_keys`
     (identity module). A key is bound to one organization, carries an explicit scope list
     (`products:ingest` is the only scope today), is revocable, rotatable (new row, old row
     revoked in the same transaction, lineage kept), and optionally expiring. Only the SHA-256
     of the secret is stored, with a 12-character display prefix; the plaintext
     (`afk_` + 240 random bits) is returned once by `POST /integrations/keys`, the rotate
     endpoint or the CLI. Lookup is by digest, followed by a constant-time digest compare.
  2. **Allow-listed routes, then scopes.** `Authorization: Bearer afk_…` resolves in
     `get_identity` before the admin token. The identity acts as `editor` but only on
     `POST /products/ingest`, `GET /products/ingest/{batch_id}` and `GET /integrations/me`;
     everything else is 403 `integration_scope_denied`, including key management, so a
     leaked key cannot mint keys. The ingest routes additionally require the `products:ingest`
     scope (`require_scope`). The organization is the key's; `X-Organization-Slug` is ignored.
     Cookie CSRF checks do not apply (there is no cookie). Admin cookie sessions manage keys;
     the service token cannot (`SERVICE_TOKEN_ALLOWED` is unchanged).
  3. **One ingest contract, versioned.** `meoscout.affilio.candidates` 2.x: an envelope
     (`batch_id`, `on_conflict`, `source`) with 1–200 candidates validated by the product
     schema itself (`ProductFieldsBase`), decimals as strings, the commission rate as a
     fraction. Envelope errors fail the request (422); per-candidate errors become `rejected`
     results so the rest of the batch lands.
  4. **Idempotency by batch, safety by identity.** `(organization, batch_id)` is unique and
     stores the per-item results; a replay returns them (`replayed: true`) and writes
     nothing; the same id with a different payload is 409 `batch_mismatch`. The batch row is
     flushed before any product write, so a concurrent duplicate blocks on the index and is
     answered with the stored results. Independently, products are matched by
     `(platform, platform_product_id)` then by normalized URL, so a resend under a new batch
     id is `unchanged`, never a duplicate; product inserts run in savepoints so a unique-index
     race with another batch degrades to the update path.
  5. **Ownership rules.** New products are `DISCOVERED`, `source=meoscout`,
     `source_ref=client_ref`, with the creating batch recorded. On a match, only
     research-derived fields are written, and only when non-empty and different. Identity
     fields are enriched when empty and otherwise never changed (`identity_differs`,
     `product_url_differs` conflicts, kept). A differing `affiliate_url` is a conflict kept by
     default and replaced only with `on_conflict: "replace"`. Status, status history,
     procurement, production, media links, channels and publishing are never touched.
  6. **Bounds and audit.** 200 items, 1 MiB body (refused before parsing), a per-credential
     sliding window (30 batches / 5 min, in-process like the login limiter). Every batch,
     replay, conflict, failure and key operation is audited; audit data carries the key
     prefix, never the secret or its hash.
- **Consequences:** MeoScout's relay (P2) holds one AFFILIO secret in its server `.env` and
  never the extension. Operators create and rotate keys as admins (API or
  `affilio.scripts.create_integration_key`). The web proxy still strips `Authorization`, so
  the relay must reach the API inside the compose network (design §7); no proxy or tunnel
  change is part of this ADR. Per-field "human edited" tracking does not exist, so
  research fields a person edited in AFFILIO can still be refreshed by MeoScout; that is the
  documented trade-off of rule 5 and a candidate for a later `locked_fields` column.
  Alternatives rejected: reusing the admin token (owner rights, no per-integration
  revocation); the extension calling AFFILIO with a person's session (not server-to-server,
  needs CSRF exemptions); mTLS (no public hop to protect once the relay is inside the
  network).
