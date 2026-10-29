import type { UploadSession } from "@affilio/shared-types";
import { describe, expect, it } from "vitest";

import {
  type BatchDefaults,
  type QueueItem,
  displayName,
  effectiveClassification,
  isBatchFinished,
  isReadyToStart,
  isVideoFile,
  itemFromFile,
  itemFromSession,
  pickStartable,
  sessionToItemState,
  summarize,
} from "@/lib/uploads/queue";

const defaults: BatchDefaults = {
  ownerId: "hang",
  productCategoryId: "mens",
  contentTypeId: "review",
  submittedForUserId: null,
  batchId: "batch-1",
};

function file(name: string, size = 1_000): File {
  return new File([new Uint8Array(size)], name, {
    type: "video/mp4",
    lastModified: 1_700_000_000_000,
  });
}

function item(name: string, patch: Partial<QueueItem> = {}): QueueItem {
  return { ...itemFromFile(file(name)), ...patch };
}

describe("queue scheduling", () => {
  it("starts at most `concurrency` items and keeps the rest queued locally", () => {
    const items = [item("a.mp4"), item("b.mp4"), item("c.mp4"), item("d.mp4")];
    const startable = pickStartable(items, defaults, 2, 20);
    expect(startable.map((i) => i.name)).toEqual(["a.mp4", "b.mp4"]);
    const [a, b, c, d] = items as [QueueItem, QueueItem, QueueItem, QueueItem];
    const next: QueueItem[] = [{ ...a, state: "uploading" }, { ...b, state: "uploading" }, c, d];
    expect(pickStartable(next, defaults, 2, 20)).toEqual([]);
    next[0] = { ...a, state: "processing" };
    expect(pickStartable(next, defaults, 2, 20).map((i) => i.name)).toEqual(["c.mp4"]);
  });

  it("never exceeds the server's active-session cap, even with free concurrency", () => {
    const holding = Array.from({ length: 20 }, (_, n) =>
      item(`p${n}.mp4`, { state: "processing", uploadId: `s${n}` }),
    );
    const queued = item("new.mp4");
    expect(pickStartable([...holding, queued], defaults, 4, 20)).toEqual([]);
    const resumable = item("old.mp4", { uploadId: "s-old" }); // resuming holds its own slot
    expect(
      pickStartable([...holding.slice(1), resumable, queued], defaults, 4, 20).map((i) => i.name),
    ).toEqual(["old.mp4", "new.mp4"]);
  });

  it("does not start a file until owner, product category and content type are known", () => {
    const i = item("a.mp4");
    expect(isReadyToStart(i, { ...defaults, contentTypeId: null })).toBe(false);
    expect(pickStartable([i], { ...defaults, ownerId: null }, 2, 20)).toEqual([]);
    const overridden = { ...i, override: { productCategoryId: "womens", contentTypeId: "try-on" } };
    expect(
      isReadyToStart(overridden, { ...defaults, productCategoryId: null, contentTypeId: null }),
    ).toBe(true);
    expect(effectiveClassification(overridden, defaults)).toEqual({
      productCategoryId: "womens",
      contentTypeId: "try-on",
    });
    expect(effectiveClassification(i, defaults)).toEqual({
      productCategoryId: "mens",
      contentTypeId: "review",
    });
  });

  it("skips items without a local file (restored sessions wait for reselection)", () => {
    const restored = item("r.mp4", { file: null, state: "needs_file", uploadId: "s1" });
    expect(pickStartable([restored, item("a.mp4")], defaults, 2, 20).map((i) => i.name)).toEqual([
      "a.mp4",
    ]);
  });
});

describe("summary and states", () => {
  it("summarises bytes and counts, excluding cancelled items, and detects a finished batch", () => {
    const items = [
      item("done.mp4", { state: "completed", offset: 1_000 }),
      item("half.mp4", { state: "uploading", offset: 500 }),
      item("dup.mp4", { state: "duplicate" }),
      item("gone.mp4", { state: "cancelled" }),
      item("bad.mp4", { state: "failed", offset: 200 }),
    ];
    const s = summarize(items);
    expect(s).toMatchObject({
      total: 5,
      completed: 1,
      duplicate: 1,
      failed: 1,
      cancelled: 1,
      active: 1,
      bytesTotal: 4_000,
    });
    expect(s.bytesDone).toBe(1_000 + 500 + 1_000 + 200);
    expect(isBatchFinished(items)).toBe(false);
    expect(isBatchFinished(items.map((i) => ({ ...i, state: "completed" as const })))).toBe(true);
    expect(isBatchFinished([])).toBe(false);
  });

  it("maps server statuses to item states (processing is not success)", () => {
    expect(sessionToItemState("uploaded")).toBe("processing");
    expect(sessionToItemState("finalizing")).toBe("processing");
    expect(sessionToItemState("completed")).toBe("completed");
    expect(sessionToItemState("duplicate")).toBe("duplicate");
    expect(sessionToItemState("expired")).toBe("failed");
    expect(sessionToItemState("uploading")).toBe("needs_file");
  });

  it("restores a session as an item that needs its file, with the server's offset and taxonomy", () => {
    const session = {
      id: "s1",
      status: "uploading",
      original_filename: "a.mp4",
      expected_size: 5_000,
      committed_offset: 2_000,
      product_category: { id: "mens", slug: "mens", name: "Men" },
      content_type: { id: "review", slug: "review", name: "Review" },
      media_asset_id: null,
      replaces_asset_id: "old",
      failure_detail: null,
    } as unknown as UploadSession;
    const restored = itemFromSession(session);
    expect(restored).toMatchObject({
      state: "needs_file",
      offset: 2_000,
      uploadId: "s1",
      file: null,
      replacesAssetId: "old",
    });
    expect(restored.override).toEqual({ productCategoryId: "mens", contentTypeId: "review" });
  });

  it("sanitises display names and filters by extension", () => {
    expect(displayName("clip\u0000\u001b[31m.mp4")).toBe("clip[31m.mp4");
    expect(displayName("x".repeat(200)).length).toBeLessThanOrEqual(120);
    expect(isVideoFile("A.MP4", [".mp4"])).toBe(true);
    expect(isVideoFile("notes.txt", [".mp4"])).toBe(false);
    expect(isVideoFile("noext", [".mp4"])).toBe(false);
  });
});
