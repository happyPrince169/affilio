import type {
  AffiliateLink,
  AffiliateLinkCreate,
  AffiliateLinkFilters,
  AffiliateLinkUpdate,
} from "@affilio/shared-types";
import { queryOptions } from "@tanstack/react-query";

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

const BASE = `${API_V1_PREFIX}/content/links`;
const JSON_HEADERS = { "Content-Type": "application/json" };
const send = <T>(path: string, body: unknown, method = "POST") =>
  apiFetch<T>(path, { method, headers: JSON_HEADERS, body: JSON.stringify(body) });

function qs(filters: AffiliateLinkFilters): string {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) if (v !== undefined) search.set(k, String(v));
  const s = search.toString();
  return s ? `?${s}` : "";
}

export const fetchLinks = (filters: AffiliateLinkFilters = {}) =>
  apiFetch<AffiliateLink[]>(`${BASE}${qs(filters)}`);
export const createLink = (body: AffiliateLinkCreate) => send<AffiliateLink>(BASE, body);
export const updateLink = (id: string, body: AffiliateLinkUpdate) =>
  send<AffiliateLink>(`${BASE}/${encodeURIComponent(id)}`, body, "PATCH");

export const linkQueries = {
  all: () => ["content", "links"] as const,
  list: (filters: AffiliateLinkFilters = {}) =>
    queryOptions({
      queryKey: [...linkQueries.all(), filters] as const,
      queryFn: () => fetchLinks(filters),
      staleTime: 5_000,
    }),
};
