import type { IngestSource } from "./domain";
import type { HandoffStatus, MediaAssetStatus } from "./media";

/**
 * Production reporting contracts (upload track U6). Mirrors
 * `apps/api/src/affilio/modules/analytics/schemas.py`. Read-only; admin only.
 */

export interface ReportPerson {
  id: string;
  email: string;
  display_name: string | null;
}

export interface ReportCategory {
  id: string;
  name: string;
  slug: string | null;
}

export interface ReportBucket {
  key: string;
  label: string;
  files: number;
  submissions: number;
  historical: number;
  pending: number;
  approved: number;
  rejected: number;
  corrected: number;
  approved_units: number;
}

export interface ProductionSummary {
  totals: ReportBucket;
  by_owner: ReportBucket[];
  by_editor: ReportBucket[];
  by_status: Record<string, number>;
  unknown_editor: number;
  unknown_owner: number;
  missing_files: number;
  periods: string[];
  /** Every period comes from first ingestion, not from editing dates. */
  period_source: "ingestion";
}

export interface ProductionRow {
  asset_id: string;
  title: string;
  file_name: string | null;
  asset_status: MediaAssetStatus;
  production_period: string | null;
  period_source: string;
  ingest_source: IngestSource;
  owner: ReportCategory | null;
  owner_source: string | null;
  uploader: ReportPerson | null;
  attributed_editor: ReportPerson | null;
  product_category: ReportCategory | null;
  content_type: ReportCategory | null;
  handoff_status: HandoffStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  submitted_at: string;
  completed_at: string | null;
  upload_id: string | null;
  revision_of_asset_id: string | null;
  work_root_id: string;
  revision_index: number;
  chain_length: number;
  is_latest_in_chain: boolean;
  approved_unit: boolean;
}

export interface ProductionRowsPage {
  items: ProductionRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface ProductionPeriod {
  period: string;
  files: number;
  submissions: number;
}

export interface ProductionReportFilters {
  period?: string;
  owner_id?: string;
  editor_id?: string;
  status?: HandoffStatus;
  attribution?: "upload" | "historical";
  product_category_id?: string;
  content_type_id?: string;
  unknown_editor?: boolean;
}
