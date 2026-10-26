# ADR 0001 — Modular monolith, not microservices

- **Status:** Accepted (2026-10-26)
- **Milestone:** M0

## Context

AFFILIO will grow from one Shopee account to a network of 100+ accounts across five
platforms, with media processing, scheduling, analytics, and an AI agent. It is built by a
very small team, starts with minimal historical data, and must validate a business workflow
before scaling. The architecture has to allow new platforms and features without rewrites,
yet stay cheap to run, debug, and reason about.

## Decision

Build a **modular monolith**:

- One FastAPI application, one Next.js application, one PostgreSQL database.
- Business logic is split into ordered domain modules (`identity → accounts → media →
  products → content → publishing → analytics → agents`) with an explicit dependency
  direction and service-layer boundaries (`docs/architecture/modules.md`).
- Each module owns its tables. Cross-module access goes through service functions.
- Infrastructure is limited to what a concrete requirement justifies: PostgreSQL now,
  Redis only when a background-processing need is confirmed, FFmpeg and NAS access in M1.

Explicitly rejected: microservices, Kafka or other brokers, Kubernetes, Elasticsearch, and
per-service databases.

## Consequences

**Positive**

- Single deploy, single transaction boundary, trivial local setup (one compose service).
- Refactoring across modules is a code change, not a cross-service migration.
- Module boundaries are enforced by convention and review now, and can become package or
  service boundaries later if (and only if) scale demands it.

**Negative / mitigations**

- Boundaries can erode. Mitigated by the ordered `DOMAIN_MODULES` list, the service-only
  access rule in `CLAUDE.md`, and review.
- Heavy work (video processing, large imports) will compete with request handling.
  Mitigated by introducing a worker process backed by Redis **when** M1/M3 shows the need,
  without changing module boundaries.
- A single database is a single point of failure. Acceptable for the MVP; backups and
  managed PostgreSQL are an operations decision for a later milestone.
