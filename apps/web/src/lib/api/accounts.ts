import type {
  Platform,
  SocialAccount,
  SocialAccountCreate,
  SocialAccountStatusChange,
  SocialAccountUpdate,
} from "@affilio/shared-types";
import { queryOptions } from "@tanstack/react-query";

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

const BASE = `${API_V1_PREFIX}/accounts`;
const JSON_HEADERS = { "Content-Type": "application/json" };
const send = <T>(path: string, body: unknown, method = "POST") =>
  apiFetch<T>(path, { method, headers: JSON_HEADERS, body: JSON.stringify(body) });

export interface AccountFilters {
  platform?: Platform;
  include_disabled?: boolean;
}

export const fetchAccounts = (filters: AccountFilters = {}) => {
  const search = new URLSearchParams();
  if (filters.platform) search.set("platform", filters.platform);
  if (filters.include_disabled !== undefined)
    search.set("include_disabled", String(filters.include_disabled));
  const s = search.toString();
  return apiFetch<SocialAccount[]>(`${BASE}${s ? `?${s}` : ""}`);
};
export const fetchAccount = (id: string) =>
  apiFetch<SocialAccount>(`${BASE}/${encodeURIComponent(id)}`);
export const registerAccount = (body: SocialAccountCreate) => send<SocialAccount>(BASE, body);
export const updateAccount = (id: string, body: SocialAccountUpdate) =>
  send<SocialAccount>(`${BASE}/${encodeURIComponent(id)}`, body, "PATCH");
export const changeAccountStatus = (id: string, body: SocialAccountStatusChange) =>
  send<SocialAccount>(`${BASE}/${encodeURIComponent(id)}/status`, body);

export const accountQueries = {
  all: () => ["accounts"] as const,
  list: (filters: AccountFilters = {}) =>
    queryOptions({
      queryKey: [...accountQueries.all(), "list", filters] as const,
      queryFn: () => fetchAccounts(filters),
      staleTime: 5_000,
    }),
  detail: (id: string) =>
    queryOptions({
      queryKey: [...accountQueries.all(), "detail", id] as const,
      queryFn: () => fetchAccount(id),
      staleTime: 5_000,
    }),
};
