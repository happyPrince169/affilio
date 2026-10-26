/**
 * Media library contracts (M1). Mirrors `apps/api/src/affilio/modules/media/schemas.py`.
 */

export const MEDIA_ROOT_KINDS = ["original", "proxy", "final", "thumbnail", "export"] as const;
export type MediaRootKind = (typeof MEDIA_ROOT_KINDS)[number];

export const MEDIA_ASSET_STATUSES = ["active", "missing", "unsupported"] as const;
export type MediaAssetStatus = (typeof MEDIA_ASSET_STATUSES)[number];

export const MEDIA_VARIANT_KINDS = ["original", "proxy", "final", "thumbnail", "export"] as const;
export type MediaVariantKind = (typeof MEDIA_VARIANT_KINDS)[number];

export type MediaVariantStatus = "present" | "missing";
export const MEDIA_ASSET_KINDS = ["footage", "final"] as const;
export type MediaAssetKind = (typeof MEDIA_ASSET_KINDS)[number];
export const HANDOFF_STATUSES = ["pending_review", "approved", "rejected"] as const;
export type HandoffStatus = (typeof HANDOFF_STATUSES)[number];
export type ThumbnailStatus = "pending" | "ready" | "failed" | "skipped";
export type ScanJobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type ScanTrigger = "manual" | "cli" | "scheduled";
export type Orientation = "vertical" | "horizontal" | "square";
export const MEDIA_SORT_KEYS = [
  "newest",
  "oldest",
  "title",
  "duration",
  "size",
  "last_seen",
] as const;
export type MediaSortKey = (typeof MEDIA_SORT_KEYS)[number];

export interface MediaRootStatus {
  key: string;
  kind: MediaRootKind;
  access: "read_only" | "read_write";
  name: string;
  configured: boolean;
  exists: boolean;
  readable: boolean;
  writable: boolean;
  scan_enabled: boolean;
  last_scan_at: string | null;
}

export interface MediaVariant {
  id: string;
  kind: MediaVariantKind;
  status: MediaVariantStatus;
  root_key: string;
  relative_path: string;
  file_name: string;
  extension: string;
  file_size: number;
  modified_at: string;
  content_hash: string | null;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  frame_rate: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  pixel_format: string | null;
  bit_depth: number | null;
  bit_rate: number | null;
  container_format: string | null;
  rotation: number | null;
  probed_at: string | null;
  probe_error: string | null;
  last_seen_at: string;
  missing_since: string | null;
}

export interface MediaAssetSummary {
  id: string;
  title: string;
  status: MediaAssetStatus;
  asset_kind: MediaAssetKind;
  handoff_status: HandoffStatus | null;
  orientation: Orientation | null;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  frame_rate: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  pixel_format: string | null;
  bit_depth: number | null;
  bit_rate: number | null;
  container_format: string | null;
  file_size: number | null;
  relative_path: string | null;
  file_name: string | null;
  has_proxy: boolean;
  thumbnail_status: ThumbnailStatus;
  thumbnail_url: string | null;
  duplicate_of_id: string | null;
  first_seen_at: string;
  last_seen_at: string;
  missing_since: string | null;
  created_at: string;
  updated_at: string;
}

export interface MediaAssetDetail extends MediaAssetSummary {
  handoff_note: string | null;
  handoff_reviewed_at: string | null;
  handoff_reviewed_by: string | null;
  probe_error: string | null;
  thumbnail_error: string | null;
  thumbnail_attempts: number;
  rotation: number | null;
  variants: MediaVariant[];
  duplicates: string[];
}

export interface PaginatedAssets {
  items: MediaAssetSummary[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface MediaStats {
  total: number;
  footage: number;
  finals: number;
  finals_pending_review: number;
  finals_approved: number;
  finals_rejected: number;
  active: number;
  missing: number;
  unsupported: number;
  duplicates: number;
  with_proxy: number;
  thumbnails_pending: number;
  thumbnails_ready: number;
  thumbnails_failed: number;
  total_bytes: number;
  total_duration_seconds: number;
}

export interface ScanErrorEntry {
  kind: string;
  path: string;
  message: string;
}

export interface ScanJob {
  id: string;
  root_key: string;
  status: ScanJobStatus;
  trigger: ScanTrigger;
  attempt: number;
  max_attempts: number;
  full_rescan: boolean;
  requested_by: string | null;
  next_attempt_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at: string | null;
  created_at: string;
  stats: Record<string, number>;
  errors: ScanErrorEntry[];
  error: string | null;
}

export interface ScanStatus {
  current: ScanJob | null;
  last_completed: ScanJob | null;
  roots: MediaRootStatus[];
  worker_hint: string;
}

export interface CreateScanRequest {
  root_key?: string;
  full_rescan?: boolean;
}

export interface HandoffDecision {
  status: HandoffStatus;
  note?: string | null;
}

export interface MediaAssetFilters {
  q?: string;
  status?: MediaAssetStatus;
  asset_kind?: MediaAssetKind;
  handoff_status?: HandoffStatus;
  orientation?: Orientation;
  has_proxy?: boolean;
  video_codec?: string;
  duplicates_only?: boolean;
  sort?: MediaSortKey;
  page?: number;
  page_size?: number;
}
