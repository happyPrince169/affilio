# ADR 0007 — Assisted publishing with immutable approved snapshots and evidence-gated PUBLISHED

- **Status:** Accepted (M3A, 2026-10-26)
- **Context:** No platform integration is authorized yet (Meta Page publishing needs OAuth
  and app review; Shopee Video publishing availability is unconfirmed). The team still needs
  a reliable, auditable workflow today, and later API publishing must slot in without
  rewriting the workflow.
- **Decision:**
  1. One durable `publication_jobs` row per campaign × media × account with a stable
     `intent_id`, an optional client `idempotency_key`, and optimistic locking by `version`.
  2. A ten-state machine (`DRAFT … NEEDS_RECONCILIATION`) owned by the service layer; adapters
     only report outcomes.
  3. Approval freezes an immutable `publication_snapshots` row; content edits after approval
     return the job to `DRAFT` and require a new approval. Publishing always uses the snapshot.
  4. `PUBLISHED` requires evidence (post URL, derived id). M3A evidence is explicitly
     `operator_reported`; `api_verified` is reserved. Duplicate evidence is rejected.
  5. Ambiguous outcomes park in `NEEDS_RECONCILIATION` for a human; retries are explicit.
  6. Adapter contract (`capabilities / validate / prepare / publish / status / reconcile`)
     with an assisted implementation and a capability-declaring Facebook Reels placeholder
     that performs no network calls.
- **Consequences:** operators publish manually from a checklist but the system of record is
  consistent and auditable; M3B can add real adapters behind the same contract and promote
  evidence to `api_verified` without schema changes; the scheduler remains a simple
  database-backed "release due" step, so no Redis or broker is needed.
