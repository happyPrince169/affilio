/**
 * Domain enumerations shared with the backend.
 * Keep values identical to the Python enums in `apps/api/src/affilio/core/enums.py`.
 */

/** Platforms AFFILIO is architected to support. Only `shopee` is in MVP scope. */
export const PLATFORMS = ["shopee", "facebook", "tiktok", "instagram", "youtube"] as const;
export type Platform = (typeof PLATFORMS)[number];

export const SOCIAL_ACCOUNT_STATUSES = [
  "pending",
  "connected",
  "disconnected",
  "disabled",
] as const;
export type SocialAccountStatus = (typeof SOCIAL_ACCOUNT_STATUSES)[number];

export const USER_STATUSES = ["invited", "active", "suspended"] as const;
export type UserStatus = (typeof USER_STATUSES)[number];

export const MEMBERSHIP_ROLES = ["owner", "admin", "editor", "viewer"] as const;
export type MembershipRole = (typeof MEMBERSHIP_ROLES)[number];
