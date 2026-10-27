import type { MediaRootStatus, ScanJob, ScanStatus } from "@affilio/shared-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type * as ClientModule from "@/lib/api/client";

// --- mocks -----------------------------------------------------------------------------------

const apiFetch = vi.fn();
vi.mock("@/lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof ClientModule>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetch(...args) };
});

let permissions: Record<string, boolean> = { trigger_scans: true };
vi.mock("@/components/auth/auth-provider", () => ({
  useAuth: () => ({ can: (permission: string) => permissions[permission] === true }),
}));

import { ScanStatusCard } from "@/components/media/scan-status-card";

// --- fixtures --------------------------------------------------------------------------------

function root(key: string, overrides: Partial<MediaRootStatus> = {}): MediaRootStatus {
  const readOnly = key !== "thumbnails" && key !== "exports";
  return {
    key,
    kind: key,
    access: readOnly ? "read_only" : "read_write",
    name: key,
    configured: true,
    exists: true,
    readable: true,
    writable: !readOnly,
    scan_enabled: readOnly,
    last_scan_at: null,
    ...overrides,
  } as MediaRootStatus;
}

function job(rootKey: string, overrides: Partial<ScanJob> = {}): ScanJob {
  return {
    id: `job-${rootKey}`,
    root_key: rootKey,
    status: "running",
    trigger: "manual",
    attempt: 1,
    max_attempts: 3,
    full_rescan: false,
    requested_by: "admin@test",
    next_attempt_at: null,
    started_at: new Date(Date.now() - 60_000).toISOString(),
    finished_at: null,
    heartbeat_at: new Date().toISOString(),
    created_at: new Date(Date.now() - 65_000).toISOString(),
    stats: { files_seen: 12, added: 3 },
    errors: [],
    error: null,
    ...overrides,
  };
}

function status(overrides: Partial<ScanStatus> = {}): ScanStatus {
  return {
    current: null,
    last_completed: null,
    by_root: [
      { root_key: "originals", current: null, last_completed: null },
      { root_key: "finals", current: null, last_completed: null },
    ],
    roots: ["originals", "proxies", "finals", "thumbnails", "exports"].map((key) => root(key)),
    worker_hint: "",
    ...overrides,
  };
}

function renderCard(initial: ScanStatus) {
  apiFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith("/media/scans/status")) return Promise.resolve(initial);
    if (url.endsWith("/cancel")) {
      const id = url.split("/").at(-2) ?? "";
      return Promise.resolve(job(id.replace("job-", ""), { id, status: "cancelled" }));
    }
    if (url.endsWith("/media/scans") && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as { root_key?: string };
      return Promise.resolve(job(body.root_key ?? "originals", { status: "queued" }));
    }
    return Promise.reject(new Error(`unexpected request ${url}`));
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <ScanStatusCard />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

function postedScanBodies(): Array<{ root_key?: string; full_rescan?: boolean }> {
  return apiFetch.mock.calls
    .filter(([url, init]) => String(url).endsWith("/media/scans") && init?.method === "POST")
    .map(([, init]) => JSON.parse(String(init.body)));
}

beforeEach(() => {
  apiFetch.mockReset();
  permissions = { trigger_scans: true };
});

// --- tests -----------------------------------------------------------------------------------

describe("ScanStatusCard", () => {
  it("offers one scan action per scannable root and posts the chosen root", async () => {
    renderCard(status());
    const finals = await screen.findByRole("button", { name: "Scan finals" });
    const originals = screen.getByRole("button", { name: "Scan now" });
    expect(originals).toBeEnabled();
    expect(finals).toBeEnabled();

    await userEvent.click(finals);

    await waitFor(() =>
      expect(postedScanBodies()).toEqual([{ root_key: "finals", full_rescan: false }]),
    );
  });

  it("disables a root's action when its mount is not available", async () => {
    renderCard(
      status({
        roots: status().roots.map((r) =>
          r.key === "finals" ? root("finals", { configured: false, readable: false }) : r,
        ),
      }),
    );
    expect(await screen.findByRole("button", { name: "Scan finals" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Scan now" })).toBeEnabled();
    expect(screen.getByText(/Root not configured/)).toBeInTheDocument();
  });

  it("blocks a duplicate request for the busy root only and offers cancellation", async () => {
    const running = job("finals");
    renderCard(
      status({
        current: running,
        by_root: [
          { root_key: "originals", current: null, last_completed: null },
          { root_key: "finals", current: running, last_completed: null },
        ],
      }),
    );
    const finalsSection = await screen.findByRole("region", { name: "Finals scan" });
    expect(finalsSection).toHaveTextContent("Scanning…");
    expect(finalsSection).toHaveTextContent("Running for");
    expect(finalsSection).toHaveTextContent("12 seen");
    expect(screen.getByRole("button", { name: "Scan finals" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Scan now" })).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: "Cancel finals scan" }));

    await waitFor(() =>
      expect(
        apiFetch.mock.calls.some(
          ([url, init]) =>
            String(url).endsWith("/scans/job-finals/cancel") && init?.method === "POST",
        ),
      ).toBe(true),
    );
    expect(postedScanBodies()).toEqual([]);
  });

  it("shows the last result and errors per root", async () => {
    renderCard(
      status({
        by_root: [
          {
            root_key: "originals",
            current: null,
            last_completed: job("originals", {
              status: "succeeded",
              finished_at: new Date().toISOString(),
              stats: { files_seen: 74, added: 74, proxies_linked: 73 },
            }),
          },
          {
            root_key: "finals",
            current: null,
            last_completed: job("finals", {
              status: "failed",
              finished_at: new Date().toISOString(),
              error: "ScanConfigurationError: Root 'finals' directory is not readable",
            }),
          },
        ],
      }),
    );
    const originals = await screen.findByRole("region", { name: "Originals scan" });
    expect(originals).toHaveTextContent("73 proxies");
    const finals = screen.getByRole("region", { name: "Finals scan" });
    expect(finals).toHaveTextContent("directory is not readable");
  });

  it("hides the controls for members without the trigger_scans permission", async () => {
    permissions = { trigger_scans: false };
    renderCard(status());
    expect(await screen.findByText(/requires the admin role/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Scan finals" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Scan now" })).not.toBeInTheDocument();
    expect(postedScanBodies()).toEqual([]);
  });
});
