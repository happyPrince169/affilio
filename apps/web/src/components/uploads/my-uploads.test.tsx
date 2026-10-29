import type { UploadSession } from "@affilio/shared-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as ClientModule from "@/lib/api/client";

const apiFetch = vi.fn();
vi.mock("@/lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof ClientModule>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetch(...args) };
});

import { MyUploads } from "@/components/uploads/my-uploads";

function upload(patch: Partial<UploadSession>): UploadSession {
  return {
    id: "u1",
    status: "completed",
    original_filename: "clip.mp4",
    expected_size: 1_000,
    chunk_bytes: 8,
    committed_offset: 1_000,
    production_owner: { id: "o", slug: "hang", name: "Hang" },
    production_owner_name: "Hang",
    product_category: { id: "p", slug: "mens", name: "Men's Fashion" },
    content_type: { id: "c", slug: "review", name: "Review" },
    batch_id: null,
    replaces_asset_id: null,
    uploaded_by_user_id: "me",
    submitted_for_user_id: null,
    media_asset_id: "asset-1",
    duplicate_of_asset_id: null,
    failure_code: null,
    failure_detail: null,
    finalize_attempts: 1,
    review: {
      handoff_status: "pending_review",
      handoff_note: null,
      handoff_reviewed_at: null,
      handoff_reviewed_by: null,
    },
    last_chunk_offset: null,
    last_chunk_sha256: null,
    asset_title: "clip",
    thumbnail_url: null,
    created_at: "2026-10-29T10:00:00Z",
    updated_at: "2026-10-29T10:00:00Z",
    uploaded_at: null,
    completed_at: "2026-10-29T10:05:00Z",
    expires_at: "2026-10-31T10:00:00Z",
    ...patch,
  };
}

function renderList(onCorrect = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MyUploads onCorrect={onCorrect} />
    </QueryClientProvider>,
  );
  return onCorrect;
}

describe("MyUploads", () => {
  beforeEach(() => apiFetch.mockReset());

  it("shows review status, reviewer feedback, revision links and a generic duplicate", async () => {
    apiFetch.mockResolvedValue([
      upload({ id: "u1" }),
      upload({
        id: "u2",
        original_filename: "take1.mp4",
        media_asset_id: "asset-2",
        review: {
          handoff_status: "rejected",
          handoff_note: "wrong colour grade",
          handoff_reviewed_at: "2026-10-29T11:00:00Z",
          handoff_reviewed_by: "admin",
        },
      }),
      upload({
        id: "u3",
        original_filename: "take2.mp4",
        media_asset_id: "asset-3",
        replaces_asset_id: "asset-2",
      }),
      upload({
        id: "u4",
        original_filename: "copy.mp4",
        status: "duplicate",
        media_asset_id: null,
        review: null,
      }),
      upload({
        id: "u5",
        original_filename: "later.mp4",
        status: "uploaded",
        media_asset_id: null,
        review: null,
      }),
    ]);
    const onCorrect = renderList();
    expect(await screen.findAllByText("Pending review")).toHaveLength(2);
    expect(screen.getByText("Rejected")).toBeInTheDocument();
    expect(screen.getByText("wrong colour grade")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Replaces an earlier version" })).toHaveAttribute(
      "href",
      "/content/asset-2",
    );
    expect(screen.getByText(/Identical content was already in the library/)).toBeInTheDocument();
    expect(screen.getAllByText("Processing").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: "Open in library" })).toHaveLength(3);
    await userEvent.click(screen.getByRole("button", { name: "Submit corrected version" }));
    expect(onCorrect).toHaveBeenCalledWith({
      replacesAssetId: "asset-2",
      title: "take1.mp4",
      ownerId: "o",
      productCategoryId: "p",
      contentTypeId: "c",
    });
  });

  it("renders an empty state and an error state", async () => {
    apiFetch.mockResolvedValueOnce([]);
    renderList();
    expect(await screen.findByTestId("my-uploads-empty")).toBeInTheDocument();
  });
});
