/**
 * Shared API contracts for AFFILIO.
 *
 * These types mirror the Pydantic response schemas exposed by `apps/api`.
 * When a backend schema changes, update the matching type here in the same change.
 * Source of truth for field semantics: the `schemas.py` modules under apps/api/src/affilio/.
 */

export * from "./api";
export * from "./domain";
export * from "./media";
export * from "./products";
export * from "./procurement";
export * from "./production";
