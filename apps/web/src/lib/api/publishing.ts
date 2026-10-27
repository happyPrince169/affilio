import type {
  CalendarResponse,
  CampaignCreate,
  CampaignDetail,
  CampaignFilters,
  CampaignUpdate,
  EngineStatus,
  EvidenceRequest,
  FailureRequest,
  PaginatedCampaigns,
  PaginatedPublications,
  PreparedPublication,
  PublicationBatchCreate,
  PublicationBatchResponse,
  PublicationContentUpdate,
  PublicationCreate,
  PublicationDetail,
  PublicationFilters,
  PublicationSummary,
  PublishingStats,
  ReconcileRequest,
  ReleaseDueResponse,
  ScheduleRequest,
  VersionedRequest,
} from "@affilio/shared-types";
import { queryOptions } from "@tanstack/react-query";

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

const BASE = `${API_V1_PREFIX}/publishing`;
const JSON_HEADERS = { "Content-Type": "application/json" };
const send = <T>(path: string, body: unknown, method = "POST") =>
  apiFetch<T>(path, { method, headers: JSON_HEADERS, body: JSON.stringify(body) });

function qs(filters: object): string {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(filters))
    if (v !== undefined && v !== "" && v !== null) search.set(k, String(v));
  const s = search.toString();
  return s ? `?${s}` : "";
}

const pub = (id: string) => `${BASE}/publications/${encodeURIComponent(id)}`;

export const fetchPublishingStats = () => apiFetch<PublishingStats>(`${BASE}/stats`);
export const fetchQueue = () => apiFetch<PublicationSummary[]>(`${BASE}/queue`);
export const fetchEngineStatus = () => apiFetch<EngineStatus>(`${BASE}/engine/status`);
export const reconcileWithProvider = (id: string, body: VersionedRequest) =>
  send<PublicationDetail>(`${pub(id)}/reconcile-provider`, body);
export const releaseDue = () =>
  apiFetch<ReleaseDueResponse>(`${BASE}/queue/release-due`, { method: "POST" });
export const fetchCalendar = (start: string, end: string) =>
  apiFetch<CalendarResponse>(`${BASE}/calendar${qs({ start, end })}`);

export const fetchCampaigns = (filters: CampaignFilters) =>
  apiFetch<PaginatedCampaigns>(`${BASE}/campaigns${qs(filters)}`);
export const fetchCampaign = (id: string) =>
  apiFetch<CampaignDetail>(`${BASE}/campaigns/${encodeURIComponent(id)}`);
export const createCampaign = (body: CampaignCreate) =>
  send<CampaignDetail>(`${BASE}/campaigns`, body);
export const updateCampaign = (id: string, body: CampaignUpdate) =>
  send<CampaignDetail>(`${BASE}/campaigns/${encodeURIComponent(id)}`, body, "PATCH");
export const addCampaignMedia = (id: string, mediaAssetIds: string[]) =>
  send<CampaignDetail>(`${BASE}/campaigns/${encodeURIComponent(id)}/media`, {
    media_asset_ids: mediaAssetIds,
  });
export const removeCampaignMedia = (id: string, assetId: string) =>
  apiFetch<CampaignDetail>(
    `${BASE}/campaigns/${encodeURIComponent(id)}/media/${encodeURIComponent(assetId)}`,
    { method: "DELETE" },
  );
export const addCampaignTargets = (id: string, accountIds: string[]) =>
  send<CampaignDetail>(`${BASE}/campaigns/${encodeURIComponent(id)}/targets`, {
    social_account_ids: accountIds,
  });
export const removeCampaignTarget = (id: string, accountId: string) =>
  apiFetch<CampaignDetail>(
    `${BASE}/campaigns/${encodeURIComponent(id)}/targets/${encodeURIComponent(accountId)}`,
    { method: "DELETE" },
  );

export const fetchPublications = (filters: PublicationFilters) =>
  apiFetch<PaginatedPublications>(`${BASE}/publications${qs(filters)}`);
export const fetchPublication = (id: string) => apiFetch<PublicationDetail>(pub(id));
export const fetchPrepared = (id: string) => apiFetch<PreparedPublication>(`${pub(id)}/prepare`);
export const createPublication = (body: PublicationCreate) =>
  send<PublicationDetail>(`${BASE}/publications`, body);
export const createPublicationsBatch = (body: PublicationBatchCreate) =>
  send<PublicationBatchResponse>(`${BASE}/publications/batch`, body);
export const updatePublicationContent = (id: string, body: PublicationContentUpdate) =>
  send<PublicationDetail>(pub(id), body, "PATCH");

export type SimpleAction =
  | "submit"
  | "return_to_draft"
  | "approve"
  | "revoke_approval"
  | "unschedule"
  | "release"
  | "start"
  | "retry"
  | "revise"
  | "revalidate"
  | "cancel";

export const runSimpleAction = (id: string, action: SimpleAction, body: VersionedRequest) =>
  send<PublicationDetail>(`${pub(id)}/${action.replace(/_/g, "-")}`, body);
export const schedulePublication = (id: string, body: ScheduleRequest) =>
  send<PublicationDetail>(`${pub(id)}/schedule`, body);
export const confirmPublication = (id: string, body: EvidenceRequest) =>
  send<PublicationDetail>(`${pub(id)}/confirm`, body);
export const failPublication = (id: string, body: FailureRequest) =>
  send<PublicationDetail>(`${pub(id)}/fail`, body);
export const reconcilePublication = (id: string, body: ReconcileRequest) =>
  send<PublicationDetail>(`${pub(id)}/reconcile`, body);

export const publishingQueries = {
  all: () => ["publishing"] as const,
  stats: () =>
    queryOptions({
      queryKey: [...publishingQueries.all(), "stats"] as const,
      queryFn: fetchPublishingStats,
      staleTime: 5_000,
    }),
  queue: () =>
    queryOptions({
      queryKey: [...publishingQueries.all(), "queue"] as const,
      queryFn: fetchQueue,
      staleTime: 5_000,
      refetchInterval: 10_000,
    }),
  engine: () =>
    queryOptions({
      queryKey: [...publishingQueries.all(), "engine"] as const,
      queryFn: fetchEngineStatus,
      staleTime: 5_000,
      refetchInterval: 10_000,
    }),
  calendar: (start: string, end: string) =>
    queryOptions({
      queryKey: [...publishingQueries.all(), "calendar", start, end] as const,
      queryFn: () => fetchCalendar(start, end),
      staleTime: 5_000,
    }),
  campaigns: (filters: CampaignFilters) =>
    queryOptions({
      queryKey: [...publishingQueries.all(), "campaigns", filters] as const,
      queryFn: () => fetchCampaigns(filters),
      staleTime: 5_000,
    }),
  campaign: (id: string) =>
    queryOptions({
      queryKey: [...publishingQueries.all(), "campaign", id] as const,
      queryFn: () => fetchCampaign(id),
      staleTime: 5_000,
    }),
  publications: (filters: PublicationFilters) =>
    queryOptions({
      queryKey: [...publishingQueries.all(), "publications", filters] as const,
      queryFn: () => fetchPublications(filters),
      staleTime: 5_000,
    }),
  publication: (id: string) =>
    queryOptions({
      queryKey: [...publishingQueries.all(), "publication", id] as const,
      queryFn: () => fetchPublication(id),
      staleTime: 2_000,
    }),
  prepared: (id: string, enabled: boolean) =>
    queryOptions({
      queryKey: [...publishingQueries.all(), "prepared", id] as const,
      queryFn: () => fetchPrepared(id),
      staleTime: 5_000,
      enabled,
    }),
};
