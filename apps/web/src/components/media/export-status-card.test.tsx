import type { AssetExportStatus, ExportJob, MediaVariant } from "@affilio/shared-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as ClientModule from "@/lib/api/client";

const apiFetch = vi.fn();
vi.mock("@/lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof ClientModule>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetch(...args) };
});

let permissions: Record<string, boolean> = { manage_exports: true };
vi.mock("@/components/auth/auth-provider", () => ({
  useAuth: () => ({ can: (permission: string) => permissions[permission] === true }),
}));

import { ExportStatusCard } from "@/components/media/export-status-card";

const ASSET = "11111111-1111-4111-8111-111111111111";

function job(overrides: Partial<ExportJob> = {}): ExportJob {
  return {
    id: "job-1",
    media_asset_id: ASSET,
    status: "queued",
    trigger: "manual",
    recipe_key: "reels-h264-v1",
    attempt: 0,
    max_attempts: 3,
    requested_by: "admin@test",
    error_code: null,
    error: null,
    stats: {},
    created_at: new Date().toISOString(),
    started_at: null,
    finished_at: null,
    heartbeat_at: null,
    ...overrides,
  };
}

function exportVariant(): MediaVariant {
  return {
    id: "var-1",
    kind: "export",
    status: "present",
    root_key: "exports",
    relative_path: "11/1111-reels-h264-v1.mp4",
    file_name: "1111-reels-h264-v1.mp4",
    extension: ".mp4",
    file_size: 12_345_678,
    modified_at: new Date().toISOString(),
    content_hash: null,
    duration_seconds: 12.5,
    width: 1080,
    height: 1920,
    frame_rate: 30,
    video_codec: "h264",
    audio_codec: "aac",
    pixel_format: "yuv420p",
    bit_depth: 8,
    bit_rate: 8_000_000,
    container_format: "mov,mp4,m4a,3gp,3g2,mj2",
    rotation: null,
    probed_at: new Date().toISOString(),
    probe_error: null,
    last_seen_at: new Date().toISOString(),
    missing_since: null,
    derived_from_variant_id: "final-1",
    recipe_key: "reels-h264-v1",
  };
}

function status(overrides: Partial<AssetExportStatus> = {}): AssetExportStatus {
  return {
    state: "not_generated",
    detail: null,
    job: null,
    export: null,
    can_export: true,
    ...overrides,
  };
}

function renderCard(value: AssetExportStatus) {
  apiFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith(`/media/assets/${ASSET}/export`) && init?.method === "POST") {
      return Promise.resolve(job());
    }
    if (url.endsWith("/cancel") && init?.method === "POST") {
      return Promise.resolve(job({ status: "cancelled" }));
    }
    return Promise.reject(new Error(`unexpected request ${url}`));
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ExportStatusCard assetId={ASSET} status={value} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiFetch.mockReset();
  permissions = { manage_exports: true };
});

describe("ExportStatusCard", () => {
  it("renders every state with its label", () => {
    const states: AssetExportStatus["state"][] = [
      "not_needed",
      "not_generated",
      "queued",
      "processing",
      "ready",
      "failed",
      "blocked",
      "stale",
    ];
    for (const state of states) {
      const { unmount } = renderCard(status({ state, can_export: state !== "not_needed" }));
      expect(screen.getByTestId("export-state")).toHaveTextContent(
        {
          not_needed: "Not needed",
          not_generated: "Not generated",
          queued: "Queued",
          processing: "Processing",
          ready: "Ready",
          failed: "Failed",
          blocked: "Blocked",
          stale: "Stale",
        }[state],
      );
      unmount();
    }
  });

  it("queues an export for a final that needs one", async () => {
    renderCard(status({ state: "not_generated", detail: "pixel format yuv420p10le is not 8-bit" }));
    expect(screen.getByText(/yuv420p10le/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create export" }));
    await waitFor(() =>
      expect(
        apiFetch.mock.calls.some(
          ([url, init]) =>
            String(url).endsWith(`/media/assets/${ASSET}/export`) && init?.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("offers re-export and shows the file for a ready export; cancel while active", async () => {
    renderCard(status({ state: "ready", export: exportVariant(), job: job({ status: "ready" }) }));
    expect(screen.getByRole("button", { name: "Re-export" })).toBeEnabled();
    expect(screen.getByText(/exports\/11\/1111-reels-h264-v1.mp4/)).toBeInTheDocument();
    expect(screen.getByText(/h264 yuv420p 1080x1920/)).toBeInTheDocument();

    renderCard(
      status({
        state: "processing",
        job: job({ status: "running", attempt: 1 }),
        can_export: false,
      }),
    );
    const cancel = screen.getByRole("button", { name: /Cancel/ });
    await userEvent.click(cancel);
    await waitFor(() =>
      expect(
        apiFetch.mock.calls.some(([url]) => String(url).endsWith("/media/exports/job-1/cancel")),
      ).toBe(true),
    );
  });

  it("never offers conversion for blocked or not-needed finals, nor to non-admins", () => {
    renderCard(status({ state: "blocked", detail: "HDR metadata", can_export: false }));
    expect(screen.queryByRole("button", { name: /export/i })).not.toBeInTheDocument();
    renderCard(status({ state: "not_needed", can_export: false }));
    expect(screen.queryByRole("button", { name: /export/i })).not.toBeInTheDocument();
    permissions = { manage_exports: false };
    renderCard(status({ state: "not_generated" }));
    expect(screen.getByText(/Admin role required/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create export" })).not.toBeInTheDocument();
  });
});
