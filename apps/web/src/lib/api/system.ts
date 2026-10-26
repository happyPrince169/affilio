import type { HealthResponse, SystemInfoResponse } from "@affilio/shared-types";
import { queryOptions } from "@tanstack/react-query";

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

export function fetchHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>(`${API_V1_PREFIX}/health`);
}

export function fetchSystemInfo(): Promise<SystemInfoResponse> {
  return apiFetch<SystemInfoResponse>(`${API_V1_PREFIX}/system/info`);
}

export const systemQueries = {
  all: () => ["system"] as const,
  health: () =>
    queryOptions({
      queryKey: [...systemQueries.all(), "health"] as const,
      queryFn: fetchHealth,
      refetchInterval: 30_000,
      retry: 1,
    }),
  info: () =>
    queryOptions({
      queryKey: [...systemQueries.all(), "info"] as const,
      queryFn: fetchSystemInfo,
      refetchInterval: 30_000,
      retry: 1,
    }),
};
