/** Affiliate link library (M3A). Mirrors `apps/api/src/affilio/modules/content/schemas.py`. */

import type { AffiliateLinkStatus, Platform } from "./domain";

export interface AffiliateLink {
  id: string;
  platform: Platform;
  product_id: string | null;
  product_name: string | null;
  label: string;
  url: string;
  source: "manual" | "import";
  status: AffiliateLinkStatus;
  notes: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface AffiliateLinkCreate {
  platform?: Platform;
  product_id?: string | null;
  label: string;
  url: string;
  notes?: string | null;
}

export interface AffiliateLinkUpdate {
  product_id?: string | null;
  label?: string;
  url?: string;
  notes?: string | null;
  status?: AffiliateLinkStatus;
}

export interface AffiliateLinkFilters {
  status?: AffiliateLinkStatus | "";
  platform?: Platform;
  product_id?: string;
  q?: string;
}
