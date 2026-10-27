/** Health probe result. Mirrors `HealthResponse` in the API. */
export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
  environment: string;
  timestamp: string;
}

/** Database connectivity check. Mirrors `DatabaseStatus` in the API. */
export interface DatabaseStatus {
  status: "ok" | "unavailable";
  dialect: string;
  latency_ms: number | null;
}

/** Facts about the Meta integration (mode and whether configured). Mirrors `MetaIntegrationInfo`. */
export interface MetaIntegrationInfo {
  mode: "live" | "mock";
  configured: boolean;
  graph_api_version: string;
}

export interface IntegrationsInfo {
  meta: MetaIntegrationInfo;
}

/** Non-sensitive runtime information. Mirrors `SystemInfoResponse` in the API. */
export interface SystemInfoResponse {
  service: string;
  version: string;
  environment: string;
  api_prefix: string;
  python_version: string;
  timestamp: string;
  database: DatabaseStatus;
  modules: string[];
  integrations: IntegrationsInfo;
}

/** Structured error envelope returned by every non-2xx API response. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: unknown;
    request_id: string | null;
  };
}
