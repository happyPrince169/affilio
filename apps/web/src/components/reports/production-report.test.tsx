import type { ProductionRow, ProductionSummary } from "@affilio/shared-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as ClientModule from "@/lib/api/client";

const apiFetch = vi.fn();
vi.mock("@/lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof ClientModule>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetch(...args) };
});

let permissions: Record<string, boolean> = { approve: true };
vi.mock("@/components/auth/auth-provider", () => ({
  useAuth: () => ({ can: (permission: string) => permissions[permission] === true }),
}));

import { ProductionReport } from "@/components/reports/production-report";

const bucket = (key: string, label: string, approved_units = 0) => ({
  key,
  label,
  files: 2,
  submissions: 1,
  historical: 1,
  pending: 1,
  approved: 1,
  rejected: 0,
  corrected: 0,
  approved_units,
});

const summary: ProductionSummary = {
  totals: {
    ...bucket("all", "All", 3),
    files: 4,
    submissions: 3,
    historical: 1,
    pending: 1,
    approved: 2,
    rejected: 1,
    corrected: 1,
  },
  by_owner: [bucket("o1", "Hang", 2), bucket("unknown", "Unknown owner")],
  by_editor: [bucket("e1", "Editor One", 2), bucket("unknown", "Unknown editor (historical)")],
  by_status: { pending_review: 1, approved: 2, rejected: 1 },
  unknown_editor: 1,
  unknown_owner: 0,
  missing_files: 0,
  periods: ["2026-11", "2026-10"],
  period_source: "ingestion",
};

function row(patch: Partial<ProductionRow>): ProductionRow {
  return {
    asset_id: "a1",
    title: "clip",
    file_name: "clip.mp4",
    asset_status: "active",
    production_period: "2026-11",
    period_source: "ingestion",
    ingest_source: "upload",
    owner: { id: "o1", name: "Hang", slug: "hang" },
    owner_source: "upload",
    uploader: { id: "e1", email: "one@test", display_name: "Editor One" },
    attributed_editor: { id: "e1", email: "one@test", display_name: "Editor One" },
    product_category: { id: "p", name: "Men's Fashion", slug: "mens" },
    content_type: { id: "c", name: "Review", slug: "review" },
    handoff_status: "approved",
    reviewed_by: "admin@test",
    reviewed_at: "2026-11-03T10:00:00Z",
    review_note: null,
    submitted_at: "2026-11-03T09:00:00Z",
    completed_at: "2026-11-03T09:05:00Z",
    upload_id: "u1",
    revision_of_asset_id: null,
    work_root_id: "a1",
    revision_index: 0,
    chain_length: 1,
    is_latest_in_chain: true,
    approved_unit: true,
    ...patch,
  };
}

function renderReport() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ProductionReport />
    </QueryClientProvider>,
  );
}

describe("ProductionReport", () => {
  beforeEach(() => {
    permissions = { approve: true };
    apiFetch.mockReset();
    apiFetch.mockImplementation((path: string) => {
      if (path.includes("/production/periods"))
        return Promise.resolve([{ period: "2026-11", files: 3, submissions: 3 }]);
      if (path.includes("/production/summary")) return Promise.resolve(summary);
      if (path.includes("/production/rows")) {
        return Promise.resolve({
          items: [
            row({ asset_id: "a1" }),
            row({
              asset_id: "a2",
              title: "take2",
              revision_of_asset_id: "a0",
              work_root_id: "a0",
              revision_index: 1,
              chain_length: 2,
              handoff_status: "rejected",
              review_note: "colour",
              approved_unit: false,
              uploader: { id: "admin", email: "admin@test", display_name: "Admin" },
            }),
            row({
              asset_id: "a3",
              title: "legacy",
              ingest_source: "scan",
              owner_source: "path",
              uploader: null,
              attributed_editor: null,
              handoff_status: "pending_review",
              reviewed_at: null,
              reviewed_by: null,
              approved_unit: false,
              upload_id: null,
            }),
          ],
          total: 120,
          page: 1,
          page_size: 50,
        });
      }
      return Promise.reject(new Error(`unexpected ${path}`));
    });
  });

  it("shows KPIs, breakdowns, badges, lineage and pagination", async () => {
    renderReport();
    // Data-dependent text first, so the KPI values below are read after the summary loaded.
    expect(await screen.findByText("Unknown editor (historical)")).toBeInTheDocument();
    const kpi = screen.getByText("Submitted (uploads)").closest("[data-slot=card]") as HTMLElement;
    expect(within(kpi).getByText("3")).toBeInTheDocument();
    const units = screen
      .getByText("Unique approved work units")
      .closest("[data-slot=card]") as HTMLElement;
    expect(within(units).getByText("3")).toBeInTheDocument();
    expect(screen.getByText(/1 file\(s\) were indexed from the NAS/)).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "clip" })).toHaveAttribute(
      "href",
      "/content/a1",
    );
    expect(screen.getByText("approved unit")).toBeInTheDocument();
    expect(screen.getByText("corrected version")).toBeInTheDocument();
    expect(screen.getByText("unknown editor")).toBeInTheDocument();
    expect(screen.getAllByText("historical").length).toBeGreaterThan(0);
    expect(screen.getByText("Editor One (on behalf)")).toBeInTheDocument();
    expect(screen.getByText("“colour”")).toBeInTheDocument();
    expect(screen.getByText(/120 video\(s\) · page 1 of 3/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("link", { name: /Export CSV/ })).toHaveAttribute(
      "href",
      expect.stringContaining("/analytics/production/rows.csv"),
    );
  });

  it("is limited to admins", () => {
    permissions = { approve: false };
    renderReport();
    expect(screen.getByText("Admins only")).toBeInTheDocument();
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
