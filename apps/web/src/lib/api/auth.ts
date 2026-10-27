import type {
  AuditEvent,
  ChangePasswordRequest,
  CurrentUser,
  LoginRequest,
  Member,
  MemberCreate,
  MemberRoleUpdate,
  Membership,
  OrganizationUpdate,
} from "@affilio/shared-types";
import { queryOptions } from "@tanstack/react-query";

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

const BASE = `${API_V1_PREFIX}/auth`;
const JSON_HEADERS = { "Content-Type": "application/json" };
const send = <T>(path: string, body: unknown, method = "POST") =>
  apiFetch<T>(path, { method, headers: JSON_HEADERS, body: JSON.stringify(body) });

export const fetchMe = () => apiFetch<CurrentUser>(`${BASE}/me`);
export const login = (body: LoginRequest) => send<CurrentUser>(`${BASE}/login`, body);
export const logout = () => apiFetch<void>(`${BASE}/logout`, { method: "POST" });
export const changePassword = (body: ChangePasswordRequest) => send<void>(`${BASE}/password`, body);
export const fetchMembers = () => apiFetch<Member[]>(`${BASE}/members`);
export const createMember = (body: MemberCreate) => send<Member>(`${BASE}/members`, body);
export const updateMemberRole = (userId: string, body: MemberRoleUpdate) =>
  send<Member>(`${BASE}/members/${encodeURIComponent(userId)}`, body, "PATCH");
export const revokeMemberSessions = (userId: string) =>
  apiFetch<void>(`${BASE}/members/${encodeURIComponent(userId)}/sessions/revoke`, {
    method: "POST",
  });
export const updateOrganization = (body: OrganizationUpdate) =>
  send<Membership>(`${BASE}/organization`, body, "PATCH");
export const fetchAudit = (params: {
  entity_type?: string;
  entity_id?: string;
  limit?: number;
}) => {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined) search.set(k, String(v));
  const s = search.toString();
  return apiFetch<AuditEvent[]>(`${BASE}/audit${s ? `?${s}` : ""}`);
};

export const authQueries = {
  all: () => ["auth"] as const,
  me: () =>
    queryOptions({
      queryKey: [...authQueries.all(), "me"] as const,
      queryFn: fetchMe,
      staleTime: 60_000,
      retry: false,
    }),
  members: () =>
    queryOptions({
      queryKey: [...authQueries.all(), "members"] as const,
      queryFn: fetchMembers,
      staleTime: 5_000,
    }),
  audit: (params: { entity_type?: string; entity_id?: string; limit?: number }) =>
    queryOptions({
      queryKey: [...authQueries.all(), "audit", params] as const,
      queryFn: () => fetchAudit(params),
      staleTime: 5_000,
    }),
};
