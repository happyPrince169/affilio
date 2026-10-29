import type { UploadSession, UploadStatus } from "@affilio/shared-types";

/**
 * Client-side upload queue (upload track U5): pure state and scheduling decisions, no I/O.
 * The runner (`runner.ts`) performs the requests; this module decides which items may run.
 *
 * Item states:
 *   queued        waiting for a free slot (no server session yet, or a session to resume)
 *   needs_file    restored after a reload; the local file must be reselected and verified
 *   creating      server session being created
 *   uploading     chunks in flight
 *   paused        stopped by the user; resumes from the server offset
 *   processing    every byte received; the worker verifies, places and registers the file
 *   completed     the worker confirmed the asset (only then is the upload a success)
 *   duplicate     the content already exists in the library (generic; no asset reference)
 *   failed        a terminal error; retry is manual
 *   cancelled     aborted by the user
 */
export type ItemState =
  | "queued"
  | "needs_file"
  | "creating"
  | "uploading"
  | "paused"
  | "processing"
  | "completed"
  | "duplicate"
  | "failed"
  | "cancelled";

export interface Classification {
  productCategoryId: string | null;
  contentTypeId: string | null;
}

export interface QueueItem {
  localId: string;
  /** The File is kept in memory only; it is never persisted. */
  file: File | null;
  name: string;
  size: number;
  lastModified: number | null;
  state: ItemState;
  uploadId: string | null;
  /** Server-confirmed committed offset. */
  offset: number;
  /** Per-file overrides; null falls back to the batch default. */
  override: Classification;
  error: string | null;
  /** Automatic retries consumed for the current chunk; bounded by the runner. */
  retries: number;
  mediaAssetId: string | null;
  replacesAssetId: string | null;
  serverStatus: UploadStatus | null;
}

export interface BatchDefaults {
  ownerId: string | null;
  productCategoryId: string | null;
  contentTypeId: string | null;
  submittedForUserId: string | null;
  batchId: string;
}

export const TERMINAL_STATES: ReadonlySet<ItemState> = new Set([
  "completed",
  "duplicate",
  "failed",
  "cancelled",
]);
export const RUNNING_STATES: ReadonlySet<ItemState> = new Set(["creating", "uploading"]);
/** States that hold a server session slot (count against the per-user session cap). */
export const SLOT_STATES: ReadonlySet<ItemState> = new Set([
  "creating",
  "uploading",
  "paused",
  "processing",
  "needs_file",
]);

export const DEFAULT_CONCURRENCY = 2;
export const MAX_CONCURRENCY = 4;
export const MAX_AUTO_RETRIES = 3;

let counter = 0;
export function newLocalId(): string {
  counter += 1;
  return `u${Date.now().toString(36)}-${counter}`;
}

/** Display-safe filename: control characters stripped, length bounded. */
export function displayName(name: string): string {
  const cleaned = name.replace(/[\x00-\x1f\x7f]/g, "").trim();
  return cleaned.length > 120 ? `${cleaned.slice(0, 117)}…` : cleaned || "(unnamed file)";
}

export function isVideoFile(name: string, allowedExtensions: string[]): boolean {
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return false;
  return allowedExtensions.includes(name.slice(dot).toLowerCase());
}

export function itemFromFile(file: File): QueueItem {
  return {
    localId: newLocalId(),
    file,
    name: file.name,
    size: file.size,
    lastModified: file.lastModified,
    state: "queued",
    uploadId: null,
    offset: 0,
    override: { productCategoryId: null, contentTypeId: null },
    error: null,
    retries: 0,
    mediaAssetId: null,
    replacesAssetId: null,
    serverStatus: null,
  };
}

/** Restore a server session (after a reload) as an item that still needs its local file. */
export function itemFromSession(session: UploadSession): QueueItem {
  const state: ItemState = sessionToItemState(session.status);
  return {
    localId: newLocalId(),
    file: null,
    name: session.original_filename,
    size: session.expected_size,
    lastModified: null,
    state,
    uploadId: session.id,
    offset: session.committed_offset,
    override: {
      productCategoryId: session.product_category.id,
      contentTypeId: session.content_type.id,
    },
    error: session.failure_detail,
    retries: 0,
    mediaAssetId: session.media_asset_id,
    replacesAssetId: session.replaces_asset_id,
    serverStatus: session.status,
  };
}

export function sessionToItemState(status: UploadStatus): ItemState {
  switch (status) {
    case "created":
    case "uploading":
      return "needs_file";
    case "uploaded":
    case "verifying":
    case "finalizing":
      return "processing";
    case "completed":
      return "completed";
    case "duplicate":
      return "duplicate";
    case "failed":
      return "failed";
    case "aborted":
      return "cancelled";
    case "expired":
      return "failed";
  }
}

export function effectiveClassification(item: QueueItem, defaults: BatchDefaults): Classification {
  return {
    productCategoryId: item.override.productCategoryId ?? defaults.productCategoryId,
    contentTypeId: item.override.contentTypeId ?? defaults.contentTypeId,
  };
}

export function isReadyToStart(item: QueueItem, defaults: BatchDefaults): boolean {
  const cls = effectiveClassification(item, defaults);
  return Boolean(defaults.ownerId && cls.productCategoryId && cls.contentTypeId);
}

/**
 * Items that may start now: queued items with a file and complete classification, up to the
 * concurrency limit and without exceeding the server's active-session cap. Excess files stay
 * queued locally and start as slots free up.
 */
export function pickStartable(
  items: QueueItem[],
  defaults: BatchDefaults,
  concurrency: number,
  maxActiveSessions: number,
): QueueItem[] {
  const running = items.filter((i) => RUNNING_STATES.has(i.state)).length;
  const holdingSlots = items.filter((i) => SLOT_STATES.has(i.state)).length;
  let freeRuns = Math.max(0, Math.min(concurrency, MAX_CONCURRENCY) - running);
  let freeSlots = Math.max(0, maxActiveSessions - holdingSlots);
  const out: QueueItem[] = [];
  for (const item of items) {
    if (freeRuns === 0 || freeSlots === 0) break;
    if (item.state !== "queued" || !item.file) continue;
    if (!isReadyToStart(item, defaults)) continue;
    out.push(item);
    freeRuns -= 1;
    if (!item.uploadId) freeSlots -= 1; // resuming an existing session holds its slot already
  }
  return out;
}

export interface Summary {
  total: number;
  completed: number;
  duplicate: number;
  failed: number;
  cancelled: number;
  processing: number;
  active: number;
  bytesTotal: number;
  bytesDone: number;
  /** 0..1 over bytes of items that are not cancelled. */
  progress: number;
}

export function summarize(items: QueueItem[]): Summary {
  const counted = items.filter((i) => i.state !== "cancelled");
  const bytesTotal = counted.reduce((n, i) => n + i.size, 0);
  const bytesDone = counted.reduce(
    (n, i) =>
      n +
      (i.state === "completed" || i.state === "processing" || i.state === "duplicate"
        ? i.size
        : i.offset),
    0,
  );
  return {
    total: items.length,
    completed: items.filter((i) => i.state === "completed").length,
    duplicate: items.filter((i) => i.state === "duplicate").length,
    failed: items.filter((i) => i.state === "failed").length,
    cancelled: items.filter((i) => i.state === "cancelled").length,
    processing: items.filter((i) => i.state === "processing").length,
    active: items.filter((i) => RUNNING_STATES.has(i.state)).length,
    bytesTotal,
    bytesDone,
    progress: bytesTotal === 0 ? 0 : Math.min(1, bytesDone / bytesTotal),
  };
}

export function isBatchFinished(items: QueueItem[]): boolean {
  return items.length > 0 && items.every((i) => TERMINAL_STATES.has(i.state));
}
