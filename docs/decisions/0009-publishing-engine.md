# ADR 0009 — Durable Reels publishing engine with a separate publisher worker

- **Status:** Accepted (M3B.2A, 2026-10-29)
- **Context:** Publishing a Reel through the Graph API is a multi-call, minutes-long process
  (session, upload, processing, publish, verification) with no provider-side idempotency key.
  Workers crash, networks time out, and the finish call may be applied without a response.
  The Synology target has few cores and a read-only finals mount. The M3A state machine and
  snapshots must stay authoritative.
- **Decision:**
  1. **Durable phase record per attempt** (`publication_uploads`) with provider identifiers,
     bytes, publish-request status, lease and retry columns; the job state machine stays at
     ten states and API work happens inside `PUBLISHING`.
  2. **One network call kind per step**, persisted before the next; the publish request is
     persisted as `sent` before it is made and is never re-sent. Unknown outcomes park the
     upload for a provider status check (admin action), never an automatic retry.
  3. **Separate publisher process** with PostgreSQL claiming (`FOR UPDATE SKIP LOCKED`),
     leases with expiry and resume-from-phase recovery, bounded thread pool, per-Page
     serialization, durable per-Page holds for rate limits and the documented daily quota,
     exponential backoff with jitter, event-driven waiting, graceful drain on SIGTERM.
  4. **Dispatch re-validates** approval, routing, classification, connection, mode and holds
     at dispatch time; assisted targets are untouched.
  5. **Mock-first**: the mock provider gains the Reels calls with scripted faults and a video
     registry so every failure mode is a deterministic test; the live calls exist but are
     refused while `META_LIVE_PUBLISHING_ENABLED=false`.
  6. **Honest guarantee**: at-least-once execution with duplicate guards; the residual
     duplicate-publication risk (lost finish + lost reconciliation) is documented and requires
     a human decision.
- **Consequences:** no Redis/broker (ADR 0004 still holds); the publisher is one more small
  container on the NAS; operators see upload phases, retries and provider errors in the
  queue; switching to live publishing is a configuration flip plus the activation checklist,
  with no schema change.
