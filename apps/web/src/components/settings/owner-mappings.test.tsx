import type { ContentCategory, OwnerMapping } from "@affilio/shared-types";
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

import { MemberOwnerMappings, ownerValues } from "@/components/settings/owner-mappings";

function category(overrides: Partial<ContentCategory>): ContentCategory {
  return {
    id: "c",
    parent_id: "root",
    slug: "x",
    name: "X",
    description: null,
    status: "active",
    sort_order: 0,
    facet: "production_owner",
    depth: 1,
    path: "Production owner / X",
    path_slugs: ["production-owner", "x"],
    children_count: 0,
    created_at: "2026-10-29T00:00:00Z",
    updated_at: "2026-10-29T00:00:00Z",
    ...overrides,
  };
}

const root = category({ id: "root", parent_id: null, slug: "production-owner", depth: 0 });
const hang = category({ id: "hang", slug: "hang", name: "Hang" });
const hao = category({ id: "hao", slug: "hao", name: "Hao" });
const old = category({ id: "old", slug: "old", name: "Old", status: "archived" });
const sports = category({
  id: "sports",
  slug: "sports",
  name: "Sports",
  facet: "product_category",
});

function mapping(overrides: Partial<OwnerMapping> = {}): OwnerMapping {
  return {
    id: "m1",
    user_id: "u1",
    user_email: "editor@example.test",
    user_display_name: "Editor",
    production_owner: { id: "hang", slug: "hang", name: "Hang", status: "active" },
    status: "active",
    created_at: "2026-10-29T00:00:00Z",
    updated_at: "2026-10-29T00:00:00Z",
    revoked_at: null,
    created_by_user_id: "admin",
    revoked_by_user_id: null,
    ...overrides,
  };
}

function renderCell(userId = "u1", canManage = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemberOwnerMappings userId={userId} canManage={canManage} />
    </QueryClientProvider>,
  );
}

describe("ownerValues", () => {
  it("keeps only active values under the production-owner root, sorted by name", () => {
    expect(ownerValues([sports, hao, old, root, hang]).map((c) => c.slug)).toEqual(["hang", "hao"]);
  });
});

describe("MemberOwnerMappings", () => {
  beforeEach(() => {
    apiFetch.mockReset();
    apiFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path.endsWith("/media/owner-mappings") && !init?.method) {
        return Promise.resolve([
          mapping(),
          mapping({
            id: "m2",
            user_id: "u2",
            production_owner: { id: "hao", slug: "hao", name: "Hao", status: "active" },
          }),
          mapping({
            id: "m3",
            production_owner: { id: "old", slug: "old", name: "Old", status: "archived" },
          }),
        ]);
      }
      if (path.includes("/content-categories")) {
        return Promise.resolve([root, hang, hao, old, sports]);
      }
      if (init?.method === "DELETE") return Promise.resolve(undefined);
      return Promise.reject(new Error(`unexpected ${init?.method ?? "GET"} ${path}`));
    });
  });

  it("shows only this member's owners, flags archived ones and offers the rest", async () => {
    renderCell();
    expect(await screen.findByText("Hang")).toBeInTheDocument();
    expect(screen.getByText("(archived)")).toBeInTheDocument();
    expect(screen.queryByText("Hao")).not.toBeInTheDocument(); // belongs to u2
    expect(screen.getByRole("combobox", { name: "Add production owner" })).toBeEnabled();
    expect(screen.getByText("Add owner…")).toBeInTheDocument();
  });

  it("revokes a mapping through the API", async () => {
    renderCell();
    await screen.findByText("Hang");
    await userEvent.click(screen.getByRole("button", { name: "Revoke Hang" }));
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith(
        expect.stringMatching(/\/media\/owner-mappings\/m1$/),
        expect.objectContaining({ method: "DELETE" }),
      ),
    );
  });

  it("renders nothing for members who cannot manage members", () => {
    renderCell("u1", false);
    expect(screen.queryByTestId("owner-mappings")).not.toBeInTheDocument();
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it("explains when the facet roots do not exist yet", async () => {
    apiFetch.mockImplementation((path: string) =>
      path.includes("/content-categories") ? Promise.resolve([sports]) : Promise.resolve([]),
    );
    renderCell();
    expect(await screen.findByText("no facet roots yet")).toBeInTheDocument();
    expect(await screen.findByText("no production owner")).toBeInTheDocument();
  });
});
