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
