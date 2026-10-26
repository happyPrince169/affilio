/** Production contracts (M2B). Mirrors `apps/api/src/affilio/modules/production/schemas.py`. */

import type { MediaAssetSummary } from "./media";
import type { ProductRef } from "./procurement";

export const PRODUCTION_STATUSES = [
  "planned",
  "scheduled",
  "filming",
  "filmed",
  "cancelled",
] as const;
export type ProductionStatus = (typeof PRODUCTION_STATUSES)[number];

export const EDITING_STATUSES = ["not_started", "in_progress", "review", "done"] as const;
export type EditingStatus = (typeof EDITING_STATUSES)[number];

export const PRODUCTION_MEDIA_ROLES = ["footage", "final", "reference"] as const;
export type ProductionMediaRole = (typeof PRODUCTION_MEDIA_ROLES)[number];

export type ProductionSortKey = "planned" | "deadline" | "newest" | "updated";

export interface JobProduct {
  id: string;
  product: ProductRef;
  purchase_order_item_id: string | null;
  order_number: string | null;
  quantity_used: number;
  notes: string | null;
  created_at: string;
}

export interface JobMedia {
  id: string;
  role: ProductionMediaRole;
  notes: string | null;
  created_at: string;
  asset: MediaAssetSummary;
}

export interface ProductionJobSummary {
  id: string;
  job_number: string;
  title: string;
  status: ProductionStatus;
  editing_status: EditingStatus;
  planned_filming_date: string | null;
  deadline: string | null;
  staff_labels: string[];
  product_count: number;
  media_count: number;
  final_count: number;
  status_changed_at: string;
  created_at: string;
  updated_at: string;
}

export interface ProductionJobDetail extends ProductionJobSummary {
  description: string | null;
  notes: string | null;
  products: JobProduct[];
  media: JobMedia[];
  allowed_transitions: ProductionStatus[];
  allowed_editing_transitions: EditingStatus[];
}

export interface PaginatedJobs {
  items: ProductionJobSummary[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface ProductionStats {
  by_status: Record<ProductionStatus, number>;
  by_editing_status: Record<EditingStatus, number>;
  upcoming: number;
  overdue: number;
}

export interface ProductionJobCreate {
  title: string;
  description?: string | null;
  planned_filming_date?: string | null;
  deadline?: string | null;
  staff_labels?: string[];
  notes?: string | null;
  product_ids?: string[];
}

export type ProductionJobUpdate = Partial<Omit<ProductionJobCreate, "product_ids">>;

export interface ProductionStatusChange {
  status?: ProductionStatus;
  editing_status?: EditingStatus;
  note?: string | null;
}

export interface JobProductInput {
  product_id: string;
  purchase_order_item_id?: string | null;
  quantity_used?: number;
  notes?: string | null;
}

export interface JobMediaInput {
  media_asset_id: string;
  role?: ProductionMediaRole;
  notes?: string | null;
}

export interface ProductionJobFilters {
  q?: string;
  status?: ProductionStatus;
  editing_status?: EditingStatus;
  product_id?: string;
  media_asset_id?: string;
  sort?: ProductionSortKey;
  page?: number;
  page_size?: number;
}
