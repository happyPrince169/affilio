import type {
  UploadChunkResponse,
  UploadCreate,
  UploadManifest,
  UploadOptions,
  UploadSession,
  UploadStatus,
} from "@affilio/shared-types";
import { queryOptions } from "@tanstack/react-query";

import {
  API_BASE_URL,
  API_V1_PREFIX,
  ApiError,
  ApiUnreachableError,
  apiFetch,
} from "@/lib/api/client";

/**
 * Editor upload sessions (upload track U5). Mirrors `/api/v1/media/uploads` from
 * `docs/architecture/editor-uploads.md` section 2.2a. Progress is server-authoritative: the
 * client always resumes from `committed_offset` as reported by the server.
 */

const UPLOADS = `${API_V1_PREFIX}/media/uploads`;
const JSON_HEADERS = { "Content-Type": "application/json" };

export const fetchUploadOptions = () => apiFetch<UploadOptions>(`${UPLOADS}/options`);

export const createUpload = (body: UploadCreate) =>
  apiFetch<UploadSession>(UPLOADS, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });

export const fetchUpload = (id: string) =>
  apiFetch<UploadSession>(`${UPLOADS}/${encodeURIComponent(id)}`);

export interface UploadListFilters {
  status?: UploadStatus;
  userId?: string;
  batchId?: string;
  limit?: number;
}

export const fetchUploads = (filters: UploadListFilters = {}) => {
  const search = new URLSearchParams();
  if (filters.status) search.set("status", filters.status);
  if (filters.userId) search.set("user_id", filters.userId);
  if (filters.batchId) search.set("batch_id", filters.batchId);
  if (filters.limit) search.set("limit", String(filters.limit));
  const query = search.toString();
  return apiFetch<UploadSession[]>(`${UPLOADS}${query ? `?${query}` : ""}`);
};

export const fetchUploadManifest = (id: string) =>
  apiFetch<UploadManifest>(`${UPLOADS}/${encodeURIComponent(id)}/manifest`);

export const completeUpload = (id: string) =>
  apiFetch<UploadSession>(`${UPLOADS}/${encodeURIComponent(id)}/complete`, { method: "POST" });

export const abortUpload = (id: string) =>
  apiFetch<void>(`${UPLOADS}/${encodeURIComponent(id)}`, { method: "DELETE" });

/**
 * One chunk at `offset`. The body is the raw slice (never JSON), with the per-chunk SHA-256
 * the server verifies before committing. Uses the same cookie/credentials rules as apiFetch;
 * `signal` lets pause and cancel abort an in-flight chunk.
 */
export async function uploadChunk(
  id: string,
  offset: number,
  chunk: Blob,
  sha256Hex: string,
  signal?: AbortSignal,
): Promise<UploadChunkResponse> {
  const url = `${API_BASE_URL}${UPLOADS}/${encodeURIComponent(id)}/chunk`;
  let response: Response;
  try {
    response = await fetch(url, {
      method: "PATCH",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/offset+octet-stream",
        "Upload-Offset": String(offset),
        "X-Upload-Chunk-Sha256": sha256Hex,
      },
      body: chunk,
      credentials: "include",
      cache: "no-store",
      signal,
    });
  } catch (cause) {
    if (signal?.aborted) throw cause;
    throw new ApiUnreachableError(API_BASE_URL, cause);
  }
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // non-JSON error body
    }
    const error =
      body && typeof body === "object" && "error" in body
        ? (body as { error: ConstructorParameters<typeof ApiError>[1] }).error
        : null;
    throw new ApiError(
      response.status,
      error,
      `Chunk upload failed with status ${response.status}`,
    );
  }
  return (await response.json()) as UploadChunkResponse;
}

export const uploadQueries = {
  all: () => ["uploads"] as const,
  options: () =>
    queryOptions({
      queryKey: [...uploadQueries.all(), "options"] as const,
      queryFn: fetchUploadOptions,
      staleTime: 60_000,
    }),
  list: (filters: UploadListFilters = {}) =>
    queryOptions({
      queryKey: [...uploadQueries.all(), "list", filters] as const,
      queryFn: () => fetchUploads(filters),
    }),
  session: (id: string) =>
    queryOptions({
      queryKey: [...uploadQueries.all(), "session", id] as const,
      queryFn: () => fetchUpload(id),
    }),
};
