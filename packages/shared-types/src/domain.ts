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

// ---------------------------------------------------------------- M3A additions

export const SOCIAL_ACCOUNT_TYPES = ["page", "profile", "group", "channel", "shop"] as const;
export type SocialAccountType = (typeof SOCIAL_ACCOUNT_TYPES)[number];

export const SOCIAL_ACCOUNT_SOURCES = ["manual", "oauth"] as const;
export type SocialAccountSource = (typeof SOCIAL_ACCOUNT_SOURCES)[number];

// ---------------------------------------------------------------- M3B.1A connections

export const CONNECTION_PROVIDERS = ["meta"] as const;
export type ConnectionProvider = (typeof CONNECTION_PROVIDERS)[number];

export const CONNECTION_STATUSES = ["active", "revoked", "expired", "invalid"] as const;
export type ConnectionStatus = (typeof CONNECTION_STATUSES)[number];

export const CREDENTIAL_KINDS = ["meta_user_long_lived", "meta_page"] as const;
export type CredentialKind = (typeof CREDENTIAL_KINDS)[number];

export const CREDENTIAL_STATUSES = ["active", "superseded", "revoked", "invalid"] as const;
export type CredentialStatus = (typeof CREDENTIAL_STATUSES)[number];

export const OAUTH_ATTEMPT_STATUSES = [
  "pending",
  "completed",
  "denied",
  "failed",
  "expired",
] as const;
export type OAuthAttemptStatus = (typeof OAUTH_ATTEMPT_STATUSES)[number];

export type MetaProviderMode = "live" | "mock";

export const PERMISSION_STATUSES = [
  "ok",
  "missing_page_task",
  "missing_scopes",
  "unknown",
  "not_connected",
] as const;
export type PermissionStatus = (typeof PERMISSION_STATUSES)[number];

export const AFFILIATE_LINK_STATUSES = ["active", "archived"] as const;
export type AffiliateLinkStatus = (typeof AFFILIATE_LINK_STATUSES)[number];

export const CAMPAIGN_STATUSES = ["draft", "active", "completed", "archived"] as const;
export type CampaignStatus = (typeof CAMPAIGN_STATUSES)[number];

export const PUBLICATION_STATUSES = [
  "draft",
  "ready_for_review",
  "approved",
  "scheduled",
  "ready_to_publish",
  "publishing",
  "published",
  "failed",
  "cancelled",
  "needs_reconciliation",
] as const;
export type PublicationStatus = (typeof PUBLICATION_STATUSES)[number];

export const PUBLICATION_ATTEMPT_STATUSES = [
  "started",
  "succeeded",
  "failed",
  "ambiguous",
] as const;
export type PublicationAttemptStatus = (typeof PUBLICATION_ATTEMPT_STATUSES)[number];

export type PublishMode = "assisted" | "api";
export type EvidenceKind = "operator_reported" | "api_verified";

// ---------------------------------------------------------------- M3A.1 additions

export const CATEGORY_STATUSES = ["active", "archived"] as const;
export type CategoryStatus = (typeof CATEGORY_STATUSES)[number];

export const CATEGORY_ASSIGNMENT_SOURCES = ["folder", "manual", "import", "ai_future"] as const;
export type CategoryAssignmentSource = (typeof CATEGORY_ASSIGNMENT_SOURCES)[number];

export const CHANNEL_MATCH_POLICIES = ["allow", "prefer", "block"] as const;
export type ChannelMatchPolicy = (typeof CHANNEL_MATCH_POLICIES)[number];
