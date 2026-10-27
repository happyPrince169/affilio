/** Distribution contracts (M3A). Mirrors `apps/api/src/affilio/modules/publishing/schemas.py`. */

import type {
  CampaignStatus,
  EvidenceKind,
  Platform,
  PublicationAttemptStatus,
  PublicationStatus,
  PublishMode,
  PublishRequestStatus,
  UploadPhase,
} from "./domain";

/** Durable state of one API publishing attempt (M3B.2A). Mirrors `UploadSummary`. */
export interface UploadSummary {
  id: string;
  attempt_id: string;
  phase: UploadPhase;
  publish_request_status: PublishRequestStatus;
  is_mock: boolean;
  progress_percent: number | null;
  bytes_sent: number;
  bytes_total: number | null;
  processing_status: string | null;
  provider_video_id: string | null;
  external_post_id: string | null;
  external_post_url: string | null;
  retry_count: number;
  max_retries: number;
  next_action_at: string;
  lease_owner: string | null;
  lease_expires_at: string | null;
  last_error: string | null;
  last_error_code: string | null;
  last_error_transient: boolean | null;
  finish_requested_at: string | null;
  published_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface WorkerStatus {
  worker_id: string;
  hostname: string;
  pid: number;
  version: string;
  max_concurrency: number;
  in_flight: number;
  started_at: string;
  last_seen_at: string;
  healthy: boolean;
}

export interface PagePublishingStatus {
  social_account_id: string;
  display_name: string;
  is_mock: boolean;
  connected: boolean;
  api_eligible: boolean;
  api_eligibility_reason: string | null;
  live_uploads: number;
  needs_reconciliation: number;
  published_last_24h: number;
  daily_quota: number;
  hold_until: string | null;
  hold_reason: string | null;
}

export interface EngineStatus {
  mode: "live" | "mock";
  live_publishing_enabled: boolean;
  workers: WorkerStatus[];
  healthy_workers: number;
  phase_counts: Record<string, number>;
  pages: PagePublishingStatus[];
  generated_at: string;
}

export type PublicationAction =
  | "submit"
  | "return_to_draft"
  | "approve"
  | "revoke_approval"
  | "schedule"
  | "unschedule"
  | "release"
  | "start"
  | "confirm"
  | "fail"
  | "report_uncertain"
  | "retry"
  | "revise"
  | "reconcile"
  | "revalidate"
  | "cancel";

// ---------------------------------------------------------------- campaigns

export interface CampaignCreate {
  name: string;
  description?: string | null;
  starts_on?: string | null;
  ends_on?: string | null;
  notes?: string | null;
  media_asset_ids?: string[];
  social_account_ids?: string[];
}

export interface CampaignUpdate {
  name?: string;
  description?: string | null;
  status?: CampaignStatus;
  starts_on?: string | null;
  ends_on?: string | null;
  notes?: string | null;
}

export interface CampaignMedia {
  media_asset_id: string;
  title: string;
  file_name: string | null;
  handoff_status: string | null;
  asset_kind: string;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  thumbnail_url: string | null;
  added_at: string;
}

export interface CampaignTarget {
  social_account_id: string;
  platform: Platform;
  display_name: string;
  handle: string | null;
  status: string;
  is_verified: boolean;
  connection_label: string;
}

export interface CampaignSummary {
  id: string;
  name: string;
  description: string | null;
  status: CampaignStatus;
  starts_on: string | null;
  ends_on: string | null;
  media_count: number;
  target_count: number;
  publication_counts: Partial<Record<PublicationStatus, number>>;
  created_at: string;
  updated_at: string;
}

export interface CampaignDetail extends CampaignSummary {
  notes: string | null;
  media: CampaignMedia[];
  targets: CampaignTarget[];
  publications: PublicationSummary[];
}

export interface PaginatedCampaigns {
  items: CampaignSummary[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface CampaignFilters {
  status?: CampaignStatus;
  q?: string;
  page?: number;
  page_size?: number;
}

// ---------------------------------------------------------------- publications

export interface PublicationContentInput {
  title?: string | null;
  caption?: string | null;
  cta?: string | null;
  hashtags?: string[];
  link_ids?: string[];
  content_notes?: string | null;
}

export interface PublicationCreate extends PublicationContentInput {
  campaign_id: string;
  media_asset_id: string;
  social_account_id: string;
  scheduled_at?: string | null;
  idempotency_key?: string | null;
}

export interface PublicationBatchItem extends PublicationContentInput {
  social_account_id: string;
  scheduled_at?: string | null;
  idempotency_key?: string | null;
}

export interface PublicationBatchCreate {
  campaign_id: string;
  media_asset_id: string;
  items: PublicationBatchItem[];
}

export interface PublicationContentUpdate extends PublicationContentInput {
  version: number;
}

export interface VersionedRequest {
  version: number;
  note?: string | null;
}

export interface ScheduleRequest extends VersionedRequest {
  scheduled_at: string;
  allow_conflict?: boolean;
}

export interface EvidenceRequest extends VersionedRequest {
  external_post_url: string;
  external_post_id?: string | null;
  published_at?: string | null;
}

export interface FailureRequest extends VersionedRequest {
  reason: string;
  outcome?: "failed" | "ambiguous";
}

export interface ReconcileRequest extends VersionedRequest {
  outcome: "published" | "not_published" | "still_unknown";
  external_post_url?: string | null;
  external_post_id?: string | null;
  published_at?: string | null;
}

export interface PublicationLink {
  affiliate_link_id: string;
  position: number;
  label: string;
  url: string;
  platform: Platform;
  status: string;
}

export interface ValidationResult {
  ok: boolean;
  errors: string[];
  warnings: string[];
}

export interface PublicationSnapshot {
  id: string;
  content_version: number;
  title: string | null;
  caption: string | null;
  cta: string | null;
  hashtags: string[];
  links: Array<Record<string, unknown>>;
  media: Record<string, unknown>;
  target: Record<string, unknown>;
  approved_by_user_id: string | null;
  approved_by_label: string;
  approved_at: string;
}

export interface PublicationAttempt {
  id: string;
  attempt_no: number;
  mode: PublishMode;
  adapter: string;
  status: PublicationAttemptStatus;
  snapshot_id: string | null;
  started_by_label: string;
  started_at: string;
  finished_at: string | null;
  detail: Record<string, unknown>;
  error: string | null;
}

export interface PublicationSummary {
  id: string;
  intent_id: string;
  campaign_id: string;
  campaign_name: string;
  media_asset_id: string;
  media_title: string;
  media_file_name: string | null;
  thumbnail_url: string | null;
  social_account_id: string;
  account_display_name: string;
  account_connection_label: string;
  platform: Platform;
  status: PublicationStatus;
  version: number;
  content_version: number;
  title: string | null;
  caption_preview: string | null;
  scheduled_at: string | null;
  published_at: string | null;
  external_post_url: string | null;
  evidence_kind: EvidenceKind | null;
  has_approved_snapshot: boolean;
  approval_stale: boolean;
  /** The video's folder/category changed after approval; review before publishing. */
  classification_stale: boolean;
  /** Current routing verdict for the target; null means eligible. Re-checked on every read. */
  target_blocked_reason: string | null;
  /** Stale classification or blocked target: start/schedule/retry are refused until resolved. */
  revalidation_required: boolean;
  revalidated_at: string | null;
  revalidated_by_label: string | null;
  link_count: number;
  attempt_count: number;
  last_error: string | null;
  status_changed_at: string;
  created_at: string;
  updated_at: string;
  // --- publishing engine (M3B.2A) ---
  publish_mode: "api" | "assisted";
  api_eligibility_reason: string | null;
  target_is_mock: boolean;
  target_hold_until: string | null;
  target_hold_reason: string | null;
  upload: UploadSummary | null;
}

export interface PublicationDetail extends PublicationSummary {
  uploads: UploadSummary[];
  caption: string | null;
  cta: string | null;
  hashtags: string[];
  content_notes: string | null;
  idempotency_key: string | null;
  links: PublicationLink[];
  approved_snapshot: PublicationSnapshot | null;
  snapshots: PublicationSnapshot[];
  attempts: PublicationAttempt[];
  external_post_id: string | null;
  confirmed_by_label: string | null;
  confirmed_at: string | null;
  confirmation_note: string | null;
  validation: ValidationResult;
  allowed_actions: PublicationAction[];
  capabilities: Record<string, unknown>;
}

export interface PaginatedPublications {
  items: PublicationSummary[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface PublicationBatchResponse {
  created: PublicationDetail[];
  reused: PublicationDetail[];
}

export interface PreparedPublication {
  mode: PublishMode;
  caption_text: string;
  links: Array<{ label?: string; url?: string; [key: string]: unknown }>;
  checklist: Array<{ key: string; label: string; done: boolean }>;
  open_url: string | null;
  instructions: string[];
  intent_id: string;
  snapshot_id: string;
  media_file_name: string | null;
  media_relative_path: string | null;
}

export interface CalendarResponse {
  timezone: string;
  range_start: string;
  range_end: string;
  items: PublicationSummary[];
}

export interface PublishingStats {
  by_status: Record<PublicationStatus, number>;
  due_now: number;
  needs_attention: number;
  campaigns_active: number;
  accounts_registered: number;
  links_active: number;
}

export interface ReleaseDueResponse {
  released: number;
  publication_ids: string[];
}

export type PublicationSortKey =
  "updated_desc" | "scheduled_asc" | "scheduled_desc" | "created_desc";

export interface PublicationFilters {
  status?: PublicationStatus;
  campaign_id?: string;
  social_account_id?: string;
  media_asset_id?: string;
  platform?: Platform;
  q?: string;
  scheduled_from?: string;
  scheduled_to?: string;
  sort?: PublicationSortKey;
  page?: number;
  page_size?: number;
}
