import type { CategoryCreate, CategoryUpdate, ContentCategory } from "@affilio/shared-types";
import { queryOptions } from "@tanstack/react-query";

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

const BASE = `${API_V1_PREFIX}/content-categories`;
const JSON_HEADERS = { "Content-Type": "application/json" };
const send = <T>(path: string, body: unknown, method = "POST") =>
  apiFetch<T>(path, { method, headers: JSON_HEADERS, body: JSON.stringify(body) });

export const fetchCategories = (includeArchived = false) =>
  apiFetch<ContentCategory[]>(`${BASE}${includeArchived ? "?include_archived=true" : ""}`);
export const createCategory = (body: CategoryCreate) => send<ContentCategory>(BASE, body);
export const updateCategory = (id: string, body: CategoryUpdate) =>
  send<ContentCategory>(`${BASE}/${encodeURIComponent(id)}`, body, "PATCH");
export const deleteCategory = (id: string) =>
  apiFetch<void>(`${BASE}/${encodeURIComponent(id)}`, { method: "DELETE" });
export const ensureUncategorized = () =>
  apiFetch<ContentCategory>(`${BASE}/ensure-uncategorized`, { method: "POST" });

export const taxonomyQueries = {
  all: () => ["taxonomy"] as const,
  categories: (includeArchived = false) =>
    queryOptions({
      queryKey: [...taxonomyQueries.all(), "categories", includeArchived] as const,
      queryFn: () => fetchCategories(includeArchived),
      staleTime: 30_000,
    }),
};
