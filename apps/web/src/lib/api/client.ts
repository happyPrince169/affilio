import type { ApiErrorBody } from "@affilio/shared-types";

import { env } from "@/lib/env";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: unknown;

  constructor(status: number, body: ApiErrorBody["error"] | null, fallbackMessage: string) {
    super(body?.message ?? fallbackMessage);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.code ?? "http_error";
    this.requestId = body?.request_id ?? null;
    this.details = body?.details;
  }
}

export class ApiUnreachableError extends Error {
  readonly baseUrl: string;

  constructor(baseUrl: string, cause: unknown) {
    super(`Could not reach the AFFILIO API at ${baseUrl}`);
    this.name = "ApiUnreachableError";
    this.baseUrl = baseUrl;
    this.cause = cause;
  }
}

export const API_BASE_URL = env.NEXT_PUBLIC_API_URL;
export const API_V1_PREFIX = "/api/v1";

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    "error" in value &&
    typeof (value as { error: unknown }).error === "object"
  );
}

/**
 * Minimal typed fetch wrapper for the AFFILIO API.
 * Throws `ApiUnreachableError` on network failure and `ApiError` on non-2xx responses.
 */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  let response: Response;

  try {
    response = await fetch(url, {
      ...init,
      headers: {
        Accept: "application/json",
        ...init?.headers,
      },
      cache: "no-store",
    });
  } catch (cause) {
    throw new ApiUnreachableError(API_BASE_URL, cause);
  }

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // Non-JSON error body; fall through with null.
    }
    throw new ApiError(
      response.status,
      isApiErrorBody(body) ? body.error : null,
      `Request failed with status ${response.status}`,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
