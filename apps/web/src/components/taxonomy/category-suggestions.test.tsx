import type { CategorySuggestion } from "@affilio/shared-types";
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

let permissions: Record<string, boolean> = { manage_accounts: true };
vi.mock("@/components/auth/auth-provider", () => ({
  useAuth: () => ({ can: (permission: string) => permissions[permission] === true }),
}));

import { CategorySuggestions } from "@/components/taxonomy/category-suggestions";

function suggestion(overrides: Partial<CategorySuggestion> = {}): CategorySuggestion {
  return {
    id: "sug-1",
    facet: "product_category",
    name: "Womens-Fashion",
    normalized_name: "womens fashion",
    root_key: "finals",
    relative_folder_path: "Hang/Womens-Fashion",
    example_relative_path: "Hang/Womens-Fashion/Try-On/a.mp4",
    status: "pending",
    seen_count: 3,
    first_seen_at: new Date().toISOString(),
    last_seen_at: new Date().toISOString(),
    decided_at: null,
    decided_by: null,
    note: null,
    category_id: null,
    category_name: null,
    ...overrides,
  };
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CategorySuggestions />
    </QueryClientProvider>,
  );
}

describe("CategorySuggestions", () => {
  beforeEach(() => {
    apiFetch.mockReset();
    permissions = { manage_accounts: true };
  });

  it("lists pending suggestions with facet, folder and count", async () => {
    apiFetch.mockResolvedValueOnce([suggestion()]);
    renderCard();
    expect(await screen.findByText("Womens-Fashion")).toBeInTheDocument();
    expect(screen.getByText("Product category")).toBeInTheDocument();
    expect(screen.getByText("Hang/Womens-Fashion")).toBeInTheDocument();
    expect(screen.getByText(/3 videos/)).toBeInTheDocument();
    expect(apiFetch.mock.calls[0]?.[0]).toContain("/media/category-suggestions?status=pending");
  });

  it("approves with the typed name and reports the reclassified count", async () => {
    apiFetch
      .mockResolvedValueOnce([suggestion()])
      .mockResolvedValueOnce({
        suggestion: suggestion({ status: "approved", category_name: "Women's Fashion" }),
        category_id: "cat-1",
        reclassified_assets: 2,
      })
      .mockResolvedValue([]);
    renderCard();
    await screen.findByText("Womens-Fashion");
    await userEvent.type(
      screen.getByLabelText("Category name for Womens-Fashion"),
      "Women's Fashion",
    );
    await userEvent.click(screen.getByRole("button", { name: /approve/i }));
    await waitFor(() => {
      const call = apiFetch.mock.calls.find(([url]) =>
        String(url).endsWith("/media/category-suggestions/sug-1/approve"),
      );
      expect(call).toBeDefined();
      expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({
        name: "Women's Fashion",
      });
    });
  });

  it("rejects without a body", async () => {
    apiFetch
      .mockResolvedValueOnce([suggestion()])
      .mockResolvedValueOnce(suggestion({ status: "rejected" }))
      .mockResolvedValue([]);
    renderCard();
    await screen.findByText("Womens-Fashion");
    await userEvent.click(screen.getByRole("button", { name: /reject/i }));
    await waitFor(() => {
      expect(
        apiFetch.mock.calls.some(([url]) =>
          String(url).endsWith("/media/category-suggestions/sug-1/reject"),
        ),
      ).toBe(true);
    });
  });

  it("hides the decision controls from non-admins and shows decided rows on request", async () => {
    permissions = {};
    apiFetch.mockResolvedValueOnce([suggestion()]).mockResolvedValueOnce([
      suggestion(),
      suggestion({
        id: "sug-2",
        name: "Hao",
        facet: "production_owner",
        status: "rejected",
        relative_folder_path: "Hao",
      }),
    ]);
    renderCard();
    await screen.findByText("Womens-Fashion");
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/show decided/i));
    expect(await screen.findByText("Rejected")).toBeInTheDocument();
    expect(screen.getAllByText("Hao")).toHaveLength(2); // name and folder
    expect(apiFetch.mock.calls[1]?.[0]).toMatch(/\/media\/category-suggestions$/);
  });
});
