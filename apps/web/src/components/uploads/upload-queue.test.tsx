import type { UploadOptions } from "@affilio/shared-types";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { UploadQueue } from "@/components/uploads/upload-queue";
import { type BatchDefaults, type QueueItem, itemFromFile, summarize } from "@/lib/uploads/queue";

const options: UploadOptions = {
  facets_enabled: true,
  can_submit_for_others: false,
  owners: [{ id: "o", slug: "hang", name: "Hang" }],
  product_categories: [{ id: "p", slug: "mens", name: "Men's Fashion" }],
  content_types: [{ id: "c", slug: "review", name: "Review" }],
  allowed_extensions: [".mp4"],
  limits: {
    chunk_bytes: 8,
    max_file_bytes: 100,
    max_active_sessions_per_user: 20,
    session_ttl_hours: 48,
    default_concurrency: 2,
  },
};
const defaults: BatchDefaults = {
  ownerId: "o",
  productCategoryId: "p",
  contentTypeId: "c",
  submittedForUserId: null,
  batchId: "b",
};

function item(name: string, patch: Partial<QueueItem>): QueueItem {
  return { ...itemFromFile(new File([new Uint8Array(100)], name)), ...patch };
}

function renderQueue(
  items: QueueItem[],
  handlers: Partial<Record<string, (id: string) => void>> = {},
) {
  const noop = () => {};
  return render(
    <UploadQueue
      items={items}
      defaults={defaults}
      options={options}
      summary={summarize(items)}
      onOverride={noop}
      onPause={handlers.onPause ?? noop}
      onResume={handlers.onResume ?? noop}
      onCancel={handlers.onCancel ?? noop}
      onRetry={handlers.onRetry ?? noop}
      onRemove={handlers.onRemove ?? noop}
      onClearFinished={noop}
    />,
  );
}

describe("UploadQueue", () => {
  it("distinguishes uploading, processing, completed, duplicate and failed rows", () => {
    renderQueue([
      item("up.mp4", { state: "uploading", offset: 50 }),
      item("proc.mp4", { state: "processing", offset: 100, uploadId: "s2" }),
      item("done.mp4", { state: "completed", offset: 100, mediaAssetId: "asset-1" }),
      item("dup.mp4", {
        state: "duplicate",
        error: "Identical content is already in the library; nothing was added",
      }),
      item("bad.mp4", {
        state: "failed",
        error: "Upload interrupted repeatedly; use Retry to continue",
      }),
    ]);
    expect(screen.getByRole("progressbar", { name: "Progress of up.mp4" })).toHaveAttribute(
      "aria-valuenow",
      "50",
    );
    expect(screen.getByText(/verifying and filing the video; it is not/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "open" })).toHaveAttribute("href", "/content/asset-1");
    // Duplicate stays generic: no asset id, filename or path of the existing video.
    expect(
      screen.getByText(/Identical content is already in the library; nothing was added\./),
    ).toBeInTheDocument();
    expect(screen.queryByText(/asset-|\/volume1/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry bad.mp4" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause up.mp4" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry done.mp4" })).not.toBeInTheDocument();
    expect(screen.getByText(/1 of 5 completed/)).toBeInTheDocument();
  });

  it("wires pause, resume, cancel, retry and remove to the right items", async () => {
    const onPause = vi.fn();
    const onResume = vi.fn();
    const onCancel = vi.fn();
    const onRetry = vi.fn();
    const onRemove = vi.fn();
    renderQueue(
      [
        item("a.mp4", { localId: "A", state: "uploading" }),
        item("b.mp4", { localId: "B", state: "paused", offset: 10 }),
        item("c.mp4", { localId: "C", state: "failed", error: "x" }),
        item("d.mp4", { localId: "D", state: "cancelled" }),
      ],
      { onPause, onResume, onCancel, onRetry, onRemove },
    );
    await userEvent.click(screen.getByRole("button", { name: "Pause a.mp4" }));
    await userEvent.click(screen.getByRole("button", { name: "Resume b.mp4" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel b.mp4" }));
    await userEvent.click(screen.getByRole("button", { name: "Retry c.mp4" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove d.mp4 from the list" }));
    expect(onPause).toHaveBeenCalledWith("A");
    expect(onResume).toHaveBeenCalledWith("B");
    expect(onCancel).toHaveBeenCalledWith("B");
    expect(onRetry).toHaveBeenCalledWith("C");
    expect(onRemove).toHaveBeenCalledWith("D");
  });

  it("explains restored sessions that need their file and sanitises names", () => {
    renderQueue([
      item("clip\u0007.mp4", { state: "needs_file", file: null, uploadId: "s1", offset: 40 }),
    ]);
    expect(screen.getByText(/Select the same file again to continue/)).toBeInTheDocument();
    expect(screen.getByTitle("clip.mp4")).toBeInTheDocument();
  });

  it("shows an empty state", () => {
    renderQueue([]);
    expect(screen.getByTestId("queue-empty")).toBeInTheDocument();
  });
});
