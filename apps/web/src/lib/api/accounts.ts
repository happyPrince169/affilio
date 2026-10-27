import type {
  ConnectionConfig,
  ConnectStartRequest,
  ConnectStartResponse,
  CredentialSummary,
  DiscoveredPage,
  DisconnectRequest,
  LinkPagesRequest,
  MockActionRequest,
  MockActionResponse,
  OAuthAttempt,
  Platform,
  PlatformConnection,
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

// ---------------------------------------------------------------- connections (M3B.1A)

const CONNECT = `${BASE}/connect/facebook`;
export const fetchConnectionConfig = () => apiFetch<ConnectionConfig>(`${CONNECT}/config`);
export const startFacebookConnection = (body: ConnectStartRequest = {}) =>
  send<ConnectStartResponse>(CONNECT, body);
export const fetchOAuthAttempts = () => apiFetch<OAuthAttempt[]>(`${CONNECT}/attempts`);
export const runMockAction = (body: MockActionRequest) =>
  send<MockActionResponse>(`${CONNECT}/mock/actions`, body);
export const fetchConnections = () => apiFetch<PlatformConnection[]>(`${BASE}/connections`);
export const fetchConnection = (id: string) =>
  apiFetch<PlatformConnection>(`${BASE}/connections/${encodeURIComponent(id)}`);
export const fetchConnectionPages = (id: string) =>
  apiFetch<DiscoveredPage[]>(`${BASE}/connections/${encodeURIComponent(id)}/pages`);
export const linkConnectionPages = (id: string, body: LinkPagesRequest) =>
  send<SocialAccount[]>(`${BASE}/connections/${encodeURIComponent(id)}/pages`, body);
export const verifyConnection = (id: string) =>
  apiFetch<PlatformConnection>(`${BASE}/connections/${encodeURIComponent(id)}/verify`, {
    method: "POST",
  });
export const revokeConnection = (id: string) =>
  apiFetch<PlatformConnection>(`${BASE}/connections/${encodeURIComponent(id)}/revoke`, {
    method: "POST",
  });
export const disconnectAccount = (id: string, body: DisconnectRequest = {}) =>
  send<SocialAccount>(`${BASE}/${encodeURIComponent(id)}/disconnect`, body);
export const verifyAccount = (id: string) =>
  apiFetch<SocialAccount>(`${BASE}/${encodeURIComponent(id)}/verify`, { method: "POST" });
export const fetchAccountCredentials = (id: string) =>
  apiFetch<CredentialSummary[]>(`${BASE}/${encodeURIComponent(id)}/credentials`);

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
  connectionConfig: () =>
    queryOptions({
      queryKey: [...accountQueries.all(), "connection-config"] as const,
      queryFn: fetchConnectionConfig,
      staleTime: 30_000,
    }),
  connections: () =>
    queryOptions({
      queryKey: [...accountQueries.all(), "connections"] as const,
      queryFn: fetchConnections,
      staleTime: 5_000,
    }),
  connection: (id: string) =>
    queryOptions({
      queryKey: [...accountQueries.all(), "connections", id] as const,
      queryFn: () => fetchConnection(id),
      staleTime: 5_000,
    }),
  connectionPages: (id: string) =>
    queryOptions({
      queryKey: [...accountQueries.all(), "connections", id, "pages"] as const,
      queryFn: () => fetchConnectionPages(id),
      staleTime: 5_000,
    }),
  credentials: (id: string) =>
    queryOptions({
      queryKey: [...accountQueries.all(), "credentials", id] as const,
      queryFn: () => fetchAccountCredentials(id),
      staleTime: 5_000,
    }),
  attempts: () =>
    queryOptions({
      queryKey: [...accountQueries.all(), "attempts"] as const,
      queryFn: fetchOAuthAttempts,
      staleTime: 5_000,
    }),
};
