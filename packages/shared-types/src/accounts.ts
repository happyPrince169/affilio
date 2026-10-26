/** Social account registry (M3A). Mirrors `apps/api/src/affilio/modules/accounts/schemas.py`. */

import type {
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
