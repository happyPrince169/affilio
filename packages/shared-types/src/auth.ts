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
