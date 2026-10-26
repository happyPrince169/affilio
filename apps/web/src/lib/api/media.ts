import type {
  CreateScanRequest,
  HandoffDecision,
  MediaAssetDetail,
  MediaAssetFilters,
  MediaRootStatus,
  MediaStats,
  PaginatedAssets,
  ScanJob,
  ScanStatus,
  AssetCategoriesUpdate,
  CategoryCount,
  FolderPreview,
  FolderReportEntry,
  FolderRule,
  FolderRuleCreate,
  FolderRuleUpdate,
} from "@affilio/shared-types";
import { queryOptions } from "@tanstack/react-query";

import { API_BASE_URL, API_V1_PREFIX, apiFetch } from "@/lib/api/client";

const MEDIA = `${API_V1_PREFIX}/media`;

function toSearchParams(filters: MediaAssetFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

/** Thumbnail URLs from the API are API-relative; prefix with the configured base. */
export function absoluteApiUrl(path: string | null): string | null {
  if (!path) return null;
  return `${API_BASE_URL}${path}`;
}

export function fetchMediaAssets(filters: MediaAssetFilters): Promise<PaginatedAssets> {
  return apiFetch<PaginatedAssets>(`${MEDIA}/assets${toSearchParams(filters)}`);
}

export function fetchMediaAsset(id: string): Promise<MediaAssetDetail> {
  return apiFetch<MediaAssetDetail>(`${MEDIA}/assets/${encodeURIComponent(id)}`);
}

export function fetchMediaStats(): Promise<MediaStats> {
  return apiFetch<MediaStats>(`${MEDIA}/stats`);
}

export function fetchMediaRoots(): Promise<MediaRootStatus[]> {
  return apiFetch<MediaRootStatus[]>(`${MEDIA}/roots`);
}

export function fetchScanStatus(): Promise<ScanStatus> {
  return apiFetch<ScanStatus>(`${MEDIA}/scans/status`);
}

export function fetchScanHistory(limit = 10): Promise<ScanJob[]> {
  return apiFetch<ScanJob[]>(`${MEDIA}/scans?limit=${limit}`);
}

export function createScan(body: CreateScanRequest = {}): Promise<ScanJob> {
  return apiFetch<ScanJob>(`${MEDIA}/scans`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function reviewHandoff(assetId: string, body: HandoffDecision): Promise<MediaAssetDetail> {
  return apiFetch<MediaAssetDetail>(`${MEDIA}/assets/${encodeURIComponent(assetId)}/handoff`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const mediaQueries = {
  all: () => ["media"] as const,
  assets: (filters: MediaAssetFilters) =>
    queryOptions({
      queryKey: [...mediaQueries.all(), "assets", filters] as const,
      queryFn: () => fetchMediaAssets(filters),
      staleTime: 5_000,
    }),
  asset: (id: string) =>
    queryOptions({
      queryKey: [...mediaQueries.all(), "asset", id] as const,
      queryFn: () => fetchMediaAsset(id),
      staleTime: 5_000,
    }),
  stats: () =>
    queryOptions({
      queryKey: [...mediaQueries.all(), "stats"] as const,
      queryFn: fetchMediaStats,
      staleTime: 5_000,
    }),
  roots: () =>
    queryOptions({
      queryKey: [...mediaQueries.all(), "roots"] as const,
      queryFn: fetchMediaRoots,
      staleTime: 30_000,
    }),
  scanStatus: () =>
    queryOptions({
      queryKey: [...mediaQueries.all(), "scans", "status"] as const,
      queryFn: fetchScanStatus,
      // Poll faster while a scan is active.
      refetchInterval: (query) => (query.state.data?.current ? 3_000 : 30_000),
    }),
  scanHistory: (limit = 10) =>
    queryOptions({
      queryKey: [...mediaQueries.all(), "scans", "history", limit] as const,
      queryFn: () => fetchScanHistory(limit),
      staleTime: 5_000,
    }),
};

// ---------------------------------------------------------------- classification (M3A.1)

const JSON_HEADERS_MEDIA = { "Content-Type": "application/json" };
const sendMedia = <T>(path: string, body: unknown, method = "POST") =>
  apiFetch<T>(path, { method, headers: JSON_HEADERS_MEDIA, body: JSON.stringify(body) });

export const setAssetCategories = (assetId: string, body: AssetCategoriesUpdate) =>
  sendMedia<MediaAssetDetail>(
    `${MEDIA}/assets/${encodeURIComponent(assetId)}/categories`,
    body,
    "PUT",
  );
export const fetchFolderRules = () => apiFetch<FolderRule[]>(`${MEDIA}/folder-rules`);
export const createFolderRule = (body: FolderRuleCreate) =>
  sendMedia<FolderRule>(`${MEDIA}/folder-rules`, body);
export const updateFolderRule = (id: string, body: FolderRuleUpdate) =>
  sendMedia<FolderRule>(`${MEDIA}/folder-rules/${encodeURIComponent(id)}`, body, "PATCH");
export const deleteFolderRule = (id: string) =>
  apiFetch<void>(`${MEDIA}/folder-rules/${encodeURIComponent(id)}`, { method: "DELETE" });
export const fetchFolderReport = () =>
  apiFetch<FolderReportEntry[]>(`${MEDIA}/folder-rules/report`);
export const fetchFolderPreview = (rootId: string, path: string) =>
  apiFetch<FolderPreview>(
    `${MEDIA}/folder-rules/preview?${new URLSearchParams({ media_root_id: rootId, relative_folder_path: path })}`,
  );
export const fetchCategoryCounts = () => apiFetch<CategoryCount[]>(`${MEDIA}/categories/counts`);

export const classificationQueries = {
  all: () => ["media", "classification"] as const,
  rules: () =>
    queryOptions({
      queryKey: [...classificationQueries.all(), "rules"] as const,
      queryFn: fetchFolderRules,
      staleTime: 5_000,
    }),
  report: () =>
    queryOptions({
      queryKey: [...classificationQueries.all(), "report"] as const,
      queryFn: fetchFolderReport,
      staleTime: 5_000,
    }),
  preview: (rootId: string | null, path: string) =>
    queryOptions({
      queryKey: [...classificationQueries.all(), "preview", rootId, path] as const,
      queryFn: () => fetchFolderPreview(rootId!, path),
      staleTime: 2_000,
      enabled: Boolean(rootId),
    }),
  counts: () =>
    queryOptions({
      queryKey: [...classificationQueries.all(), "counts"] as const,
      queryFn: fetchCategoryCounts,
      staleTime: 5_000,
    }),
};
