import type {
  JobMediaInput,
  JobProductInput,
  PaginatedJobs,
  ProductionJobCreate,
  ProductionJobDetail,
  ProductionJobFilters,
  ProductionJobUpdate,
  ProductionStats,
  ProductionStatusChange,
} from "@affilio/shared-types";
import { queryOptions } from "@tanstack/react-query";

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

const BASE = `${API_V1_PREFIX}/production`;
const JSON_HEADERS = { "Content-Type": "application/json" };
const send = <T>(path: string, body: unknown, method = "POST") =>
  apiFetch<T>(path, { method, headers: JSON_HEADERS, body: JSON.stringify(body) });

function qs(filters: ProductionJobFilters): string {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(filters))
    if (v !== undefined && v !== "") search.set(k, String(v));
  const s = search.toString();
  return s ? `?${s}` : "";
}

export const fetchProductionStats = () => apiFetch<ProductionStats>(`${BASE}/stats`);
export const fetchJobs = (filters: ProductionJobFilters) =>
  apiFetch<PaginatedJobs>(`${BASE}/jobs${qs(filters)}`);
export const fetchJob = (id: string) =>
  apiFetch<ProductionJobDetail>(`${BASE}/jobs/${encodeURIComponent(id)}`);
export const createJob = (body: ProductionJobCreate) =>
  send<ProductionJobDetail>(`${BASE}/jobs`, body);
export const updateJob = (id: string, body: ProductionJobUpdate) =>
  send<ProductionJobDetail>(`${BASE}/jobs/${encodeURIComponent(id)}`, body, "PATCH");
export const changeJobStatus = (id: string, body: ProductionStatusChange) =>
  send<ProductionJobDetail>(`${BASE}/jobs/${encodeURIComponent(id)}/status`, body);
export const addJobProduct = (id: string, body: JobProductInput) =>
  send<ProductionJobDetail>(`${BASE}/jobs/${encodeURIComponent(id)}/products`, body);
export const removeJobProduct = (id: string, productId: string) =>
  apiFetch<ProductionJobDetail>(
    `${BASE}/jobs/${encodeURIComponent(id)}/products/${encodeURIComponent(productId)}`,
    { method: "DELETE" },
  );
export const addJobMedia = (id: string, body: JobMediaInput) =>
  send<ProductionJobDetail>(`${BASE}/jobs/${encodeURIComponent(id)}/media`, body);
export const removeJobMedia = (id: string, assetId: string) =>
  apiFetch<ProductionJobDetail>(
    `${BASE}/jobs/${encodeURIComponent(id)}/media/${encodeURIComponent(assetId)}`,
    { method: "DELETE" },
  );

export const productionQueries = {
  all: () => ["production"] as const,
  stats: () =>
    queryOptions({
      queryKey: [...productionQueries.all(), "stats"] as const,
      queryFn: fetchProductionStats,
      staleTime: 5_000,
    }),
  jobs: (filters: ProductionJobFilters) =>
    queryOptions({
      queryKey: [...productionQueries.all(), "jobs", filters] as const,
      queryFn: () => fetchJobs(filters),
      staleTime: 5_000,
    }),
  job: (id: string) =>
    queryOptions({
      queryKey: [...productionQueries.all(), "job", id] as const,
      queryFn: () => fetchJob(id),
      staleTime: 5_000,
    }),
};
