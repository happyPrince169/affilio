/** Social account registry (M3A). Mirrors `apps/api/src/affilio/modules/accounts/schemas.py`. */

import type {
  ConnectionProvider,
  ConnectionStatus,
  CredentialKind,
  CredentialStatus,
  MetaProviderMode,
  OAuthAttemptStatus,
  PermissionStatus,
  Platform,
  SocialAccountSource,
  SocialAccountStatus,
  SocialAccountType,
} from "./domain";

export interface SocialAccount {
  id: string;
  platform: Platform;
  account_type: SocialAccountType;
  display_name: string;
  handle: string | null;
  external_account_id: string | null;
  profile_url: string | null;
  status: SocialAccountStatus;
  source: SocialAccountSource;
  /** True only after an OAuth authorization succeeded (M3B). Manual records are always false. */
  is_verified: boolean;
  connection_label: string;
  capabilities: Record<string, unknown>;
  notes: string | null;
  registered_by: string | null;
  created_at: string;
  updated_at: string;
  // --- connection facts (M3B.1A); never a token ---
  connection_id: string | null;
  connected_at: string | null;
  last_verified_at: string | null;
  disconnect_reason: string | null;
  page_tasks: string[] | null;
  /** True when the connection came from the mock provider: never a real Facebook Page. */
  is_mock: boolean;
  can_publish: boolean;
  permission_status: PermissionStatus;
}

export interface SocialAccountCreate {
  platform?: Platform;
  account_type?: SocialAccountType;
  display_name: string;
  handle?: string | null;
  external_account_id?: string | null;
  profile_url?: string | null;
  notes?: string | null;
}

export interface SocialAccountUpdate {
  display_name?: string;
  handle?: string | null;
  external_account_id?: string | null;
  profile_url?: string | null;
  notes?: string | null;
}

export interface SocialAccountStatusChange {
  status: "disabled" | "disconnected";
  note?: string | null;
}

// ---------------------------------------------------------------- connections (M3B.1A)

/** Mirrors `ConnectionConfigResponse`: booleans and setting *names* only, never values. */
export interface ConnectionConfig {
  provider: ConnectionProvider;
  mode: MetaProviderMode;
  configured: boolean;
  vault_ready: boolean;
  missing: string[];
  scopes: string[];
  mock_scenarios: string[];
  graph_api_version: string;
}

export interface ConnectStartRequest {
  mock_scenario?: string | null;
}

export interface ConnectStartResponse {
  authorize_url: string;
  state_expires_at: string;
  mode: MetaProviderMode;
  mock_scenario: string | null;
}

export interface PlatformConnection {
  id: string;
  provider: ConnectionProvider;
  external_user_id: string;
  external_user_name: string | null;
  status: ConnectionStatus;
  granted_scopes: string[];
  missing_scopes: string[];
  token_expires_at: string | null;
  data_access_expires_at: string | null;
  is_mock: boolean;
  connected_by_label: string;
  connected_at: string;
  last_checked_at: string | null;
  last_error: string | null;
  page_count: number;
  linked_account_count: number;
  created_at: string;
  updated_at: string;
}

export interface DiscoveredPage {
  id: string;
  name: string;
  category: string | null;
  tasks: string[];
  has_publish_task: boolean;
  can_publish: boolean;
  linked_account_id: string | null;
  linked_status: string | null;
  linked_source: string | null;
  linked_connected: boolean;
}

export interface LinkPagesRequest {
  page_ids: string[];
}

export interface DisconnectRequest {
  reason?: string | null;
}

/** Stored credential metadata. The ciphertext and the plaintext are never exposed. */
export interface CredentialSummary {
  id: string;
  kind: CredentialKind;
  version: number;
  status: CredentialStatus;
  key_id: string;
  scopes: string[];
  page_tasks: string[] | null;
  issued_at: string;
  expires_at: string | null;
  data_access_expires_at: string | null;
  last_validated_at: string | null;
  last_used_at: string | null;
  invalidated_reason: string | null;
  is_mock: boolean;
  created_by_label: string;
}

export interface OAuthAttempt {
  id: string;
  status: OAuthAttemptStatus;
  error: string | null;
  mock_scenario: string | null;
  connection_id: string | null;
  created_at: string;
  expires_at: string;
  consumed_at: string | null;
}

export type MockActionName =
  "revoke_page_token" | "revoke_user_token" | "outage_on" | "outage_off" | "reset";

export interface MockActionRequest {
  action: MockActionName;
  account_id?: string | null;
  connection_id?: string | null;
}

export interface MockActionResponse {
  action: string;
  outage: boolean;
  revoked_tokens: number;
}
