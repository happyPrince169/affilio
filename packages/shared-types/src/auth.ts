/** Authentication contracts (M3A). Mirrors `apps/api/src/affilio/modules/identity/schemas.py`. */

import type { MembershipRole, UserStatus } from "./domain";

export interface LoginRequest {
  email: string;
  password: string;
  organization_slug?: string | null;
}

export interface Membership {
  organization_id: string;
  organization_slug: string;
  organization_name: string;
  timezone: string;
  role: MembershipRole;
}

export interface Permissions {
  view: boolean;
  edit: boolean;
  approve: boolean;
  manage_accounts: boolean;
  manage_members: boolean;
  trigger_scans: boolean;
  manage_exports: boolean;
}

export interface CurrentUser {
  id: string;
  email: string;
  display_name: string | null;
  status: UserStatus;
  identity_kind: "user" | "service";
  organization: Membership;
  memberships: Membership[];
  permissions: Permissions;
  session_expires_at: string | null;
}

export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}

export interface MemberCreate {
  email: string;
  password: string;
  display_name?: string | null;
  role?: Exclude<MembershipRole, "owner">;
}

export interface MemberRoleUpdate {
  role: Exclude<MembershipRole, "owner">;
}

export interface Member {
  user_id: string;
  email: string;
  display_name: string | null;
  status: UserStatus;
  role: MembershipRole;
  last_login_at: string | null;
  created_at: string;
}

export interface OrganizationUpdate {
  name?: string | null;
  timezone?: string | null;
}

export interface AuditEvent {
  id: string;
  actor_kind: "user" | "service" | "system";
  actor_user_id: string | null;
  actor_label: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  data: Record<string, unknown>;
  request_id: string | null;
  created_at: string;
}

// ---------------------------------------------------------------- integration API keys (ADR 0013)

export const INTEGRATION_SCOPES = ["products:ingest"] as const;
export type IntegrationScope = (typeof INTEGRATION_SCOPES)[number];
export type IntegrationKeyState = "active" | "revoked" | "expired";

export interface IntegrationKeyCreate {
  name: string;
  scopes?: IntegrationScope[];
  expires_in_days?: number | null;
}

export interface IntegrationKey {
  id: string;
  name: string;
  /** First characters of the secret (`afk_` + 8), for telling keys apart. */
  prefix: string;
  scopes: string[];
  state: IntegrationKeyState;
  created_by_user_id: string | null;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  revoked_by_user_id: string | null;
  rotated_from_key_id: string | null;
}

/** Returned once by create and rotate; `secret` is never retrievable again. */
export interface IntegrationKeyCreated {
  key: IntegrationKey;
  secret: string;
}

export interface IntegrationMe {
  integration: {
    id: string;
    name: string;
    prefix: string;
    scopes: string[];
    created_at: string;
    last_used_at: string | null;
    expires_at: string | null;
  };
  organization: { id: string; slug: string; name: string };
}
