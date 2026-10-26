/**
 * Product catalog contracts (M2). Mirrors `apps/api/src/affilio/modules/products/schemas.py`.
 * Decimal fields are serialized as strings by the API.
 */

import type { MediaAssetSummary } from "./media";
import type { Platform } from "./domain";

export const PRODUCT_STATUSES = [
  "discovered",
  "shortlisted",
  "testing",
  "winner",
  "paused",
  "archived",
] as const;
export type ProductStatus = (typeof PRODUCT_STATUSES)[number];

export const PRODUCT_SORT_KEYS = [
  "newest",
  "oldest",
  "name",
  "updated",
  "price",
  "commission",
] as const;
export type ProductSortKey = (typeof PRODUCT_SORT_KEYS)[number];

export type ProductSource = "manual" | "meoscout";

export interface ProductSummary {
  id: string;
  name: string;
  platform: Platform;
  status: ProductStatus;
  source: ProductSource;
  source_ref: string | null;
  product_url: string | null;
  affiliate_url: string | null;
  platform_product_id: string | null;
  seller_name: string | null;
  category: string | null;
  images: string[];
  currency: string | null;
  observed_price: string | null;
  price_observed_at: string | null;
  estimated_commission_rate: string | null;
  estimated_commission_amount: string | null;
  media_count: number;
  status_changed_at: string;
  created_at: string;
  updated_at: string;
}

export interface ProductStatusEvent {
  id: string;
  from_status: ProductStatus | null;
  to_status: ProductStatus;
  note: string | null;
  actor: string;
  confirmed: boolean;
  created_at: string;
}

export interface ProductDetail extends ProductSummary {
  research_notes: string | null;
  status_events: ProductStatusEvent[];
  allowed_transitions: ProductStatus[];
}

export interface PaginatedProducts {
  items: ProductSummary[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface ProductStats {
  total: number;
  by_status: Record<ProductStatus, number>;
  with_media: number;
  with_affiliate_url: number;
  with_estimated_commission: number;
}

export interface ProductWrite {
  name: string;
  platform?: Platform;
  product_url?: string | null;
  affiliate_url?: string | null;
  platform_product_id?: string | null;
  seller_name?: string | null;
  category?: string | null;
  images?: string[];
  currency?: string | null;
  observed_price?: string | number | null;
  price_observed_at?: string | null;
  estimated_commission_rate?: string | number | null;
  estimated_commission_amount?: string | number | null;
  research_notes?: string | null;
}

export interface ProductCreate extends ProductWrite {
  status?: Exclude<ProductStatus, "winner">;
}

export type ProductUpdate = Partial<ProductWrite>;

export interface ProductStatusChange {
  status: ProductStatus;
  note?: string | null;
  confirmed?: boolean;
}

export interface ProductMediaLink {
  id: string;
  product_id: string;
  media_asset_id: string;
  is_primary: boolean;
  notes: string | null;
  created_at: string;
  asset: MediaAssetSummary;
}

export interface ProductMediaLinkCreate {
  media_asset_id: string;
  is_primary?: boolean;
  notes?: string | null;
}

export interface ProductMediaLinkUpdate {
  is_primary?: boolean;
  notes?: string | null;
}

export interface LinkedProduct {
  link_id: string;
  is_primary: boolean;
  notes: string | null;
  created_at: string;
  product: ProductSummary;
}

export interface ProductFilters {
  q?: string;
  status?: ProductStatus;
  platform?: Platform;
  category?: string;
  has_media?: boolean;
  sort?: ProductSortKey;
  page?: number;
  page_size?: number;
}

// ---------------------------------------------------------------- MeoScout import

export type ImportRowAction = "create" | "update" | "skip" | "error";

export interface ImportRowPreview {
  row_number: number;
  action: ImportRowAction;
  name: string | null;
  reason: string | null;
  existing_product_id: string | null;
}

export interface ImportPreview {
  file_name: string;
  file_sha256: string;
  delimiter: string;
  headers: string[];
  suggested_mapping: Record<string, string>;
  mapping: Record<string, string>;
  target_fields: string[];
  row_count: number;
  counts: Record<ImportRowAction, number>;
  rows: ImportRowPreview[];
  already_imported: boolean;
  previous_import_id: string | null;
}

export interface ProductImport {
  id: string;
  source: ProductSource;
  status: "completed" | "failed" | "skipped";
  file_name: string;
  file_sha256: string;
  mapping: Record<string, string>;
  on_duplicate: string;
  row_count: number;
  created_count: number;
  updated_count: number;
  skipped_count: number;
  failed_count: number;
  errors: { row: number; name: string | null; message: string | null }[];
  duplicate_of_import_id: string | null;
  actor: string;
  created_at: string;
}
