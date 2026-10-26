import type {
  ChannelProfile,
  ChannelProfileUpdate,
  RecommendationsResponse,
} from "@affilio/shared-types";
import { queryOptions } from "@tanstack/react-query";

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

const JSON_HEADERS = { "Content-Type": "application/json" };

export const fetchChannelProfile = (accountId: string) =>
  apiFetch<ChannelProfile>(
    `${API_V1_PREFIX}/accounts/${encodeURIComponent(accountId)}/channel-profile`,
  );
export const replaceChannelProfile = (accountId: string, body: ChannelProfileUpdate) =>
  apiFetch<ChannelProfile>(
    `${API_V1_PREFIX}/accounts/${encodeURIComponent(accountId)}/channel-profile`,
    {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    },
  );
export const fetchRecommendations = (mediaAssetId: string, campaignId?: string) => {
  const search = new URLSearchParams({ media_asset_id: mediaAssetId });
  if (campaignId) search.set("campaign_id", campaignId);
  return apiFetch<RecommendationsResponse>(
    `${API_V1_PREFIX}/distribution/recommendations?${search}`,
  );
};

export const routingQueries = {
  all: () => ["routing"] as const,
  profile: (accountId: string) =>
    queryOptions({
      queryKey: [...routingQueries.all(), "profile", accountId] as const,
      queryFn: () => fetchChannelProfile(accountId),
      staleTime: 10_000,
    }),
  recommendations: (mediaAssetId: string | null, campaignId?: string) =>
    queryOptions({
      queryKey: [
        ...routingQueries.all(),
        "recommendations",
        mediaAssetId,
        campaignId ?? null,
      ] as const,
      queryFn: () => fetchRecommendations(mediaAssetId!, campaignId),
      staleTime: 5_000,
      enabled: Boolean(mediaAssetId),
    }),
};
