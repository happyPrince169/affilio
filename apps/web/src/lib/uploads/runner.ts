import type { UploadChunkResponse, UploadCreate, UploadSession } from "@affilio/shared-types";

import { ApiError, ApiUnreachableError } from "@/lib/api/client";
import { sha256Hex } from "@/lib/uploads/sha256";
import { MAX_AUTO_RETRIES, type ItemState } from "@/lib/uploads/queue";

/**
 * Transfers one file through the U4 protocol (upload track U5):
 * create (or resume) → chunks at the server's committed offset → complete.
 *
 * Rules: the server offset is the only truth (every response and every 409 re-syncs it);
 * each chunk carries its SHA-256; a retry of the same chunk is idempotent on the server;
 * transient failures (network, 5xx, 423) back off and retry a bounded number of times; a
 * pause or cancel aborts the in-flight chunk and the server simply keeps the committed
 * offset. The runner never buffers more than one chunk.
 */

export interface RunnerApi {
  createUpload: (body: UploadCreate) => Promise<UploadSession>;
  fetchUpload: (id: string) => Promise<UploadSession>;
  uploadChunk: (
    id: string,
    offset: number,
    chunk: Blob,
    sha256: string,
    signal?: AbortSignal,
  ) => Promise<UploadChunkResponse>;
  completeUpload: (id: string) => Promise<UploadSession>;
}

export interface RunnerEvents {
  /** Session created or resumed; `chunkBytes` is the server's chunk size. */
  onSession: (session: UploadSession) => void;
  onProgress: (offset: number) => void;
  onDone: (outcome: RunnerOutcome) => void;
}

export type RunnerOutcome =
  | { state: "processing"; session: UploadSession }
  | { state: "paused"; offset: number }
  | { state: "cancelled" }
  | { state: Extract<ItemState, "completed" | "duplicate" | "failed">; session: UploadSession }
  | { state: "failed"; error: string; code: string | null };

export interface RunnerOptions {
  sleep?: (ms: number) => Promise<void>;
  hash?: (blob: Blob) => Promise<string>;
}

const PAUSE = "paused";
const CANCEL = "cancelled";
const RETRYABLE_HTTP = new Set([423, 500, 502, 503, 504]);

function backoffMs(attempt: number): number {
  return Math.min(8_000, 1_000 * 2 ** attempt);
}

export class ItemRunner {
  private controller = new AbortController();
  private stopReason: typeof PAUSE | typeof CANCEL | null = null;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly hash: (blob: Blob) => Promise<string>;

  constructor(
    private readonly file: File,
    private readonly api: RunnerApi,
    private readonly events: RunnerEvents,
    options: RunnerOptions = {},
  ) {
    this.sleep = options.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
    this.hash = options.hash ?? sha256Hex;
  }

  pause(): void {
    this.stopReason = PAUSE;
    this.controller.abort(PAUSE);
  }

  cancel(): void {
    this.stopReason = CANCEL;
    this.controller.abort(CANCEL);
  }

  /** `uploadId` null → create a session from `body`; otherwise resume that session. */
  async run(uploadId: string | null, body: UploadCreate | null): Promise<void> {
    try {
      const session = uploadId
        ? await this.api.fetchUpload(uploadId)
        : await this.api.createUpload(body as UploadCreate);
      this.events.onSession(session);
      if (this.stopReason) return this.stopped(session.committed_offset);
      const terminal = this.terminalOutcome(session);
      if (terminal) return this.events.onDone(terminal);
      if (!["created", "uploading"].includes(session.status)) {
        return this.events.onDone({ state: "processing", session });
      }
      let offset = session.committed_offset;
      this.events.onProgress(offset);
      let retries = 0;
      while (offset < this.file.size) {
        if (this.stopReason) return this.stopped(offset);
        const slice = this.file.slice(
          offset,
          Math.min(offset + session.chunk_bytes, this.file.size),
        );
        const digest = await this.hash(slice);
        if (this.stopReason) return this.stopped(offset);
        try {
          const response = await this.api.uploadChunk(
            session.id,
            offset,
            slice,
            digest,
            this.controller.signal,
          );
          offset = response.committed_offset;
          retries = 0;
          this.events.onProgress(offset);
        } catch (error) {
          if (this.stopReason) return this.stopped(offset);
          const decision = this.classify(error);
          if (decision.kind === "resync") {
            offset = decision.offset ?? (await this.api.fetchUpload(session.id)).committed_offset;
            this.events.onProgress(offset);
            retries += 1;
          } else if (decision.kind === "retry") {
            retries += 1;
          } else {
            return this.events.onDone({
              state: "failed",
              error: decision.message,
              code: decision.code,
            });
          }
          if (retries > MAX_AUTO_RETRIES) {
            return this.events.onDone({
              state: "failed",
              error: "Upload interrupted repeatedly; use Retry to continue",
              code: "retries_exhausted",
            });
          }
          await this.sleep(backoffMs(retries));
        }
      }
      const completed = await this.api.completeUpload(session.id);
      this.events.onDone(
        this.terminalOutcome(completed) ?? { state: "processing", session: completed },
      );
    } catch (error) {
      if (this.stopReason) return this.stopped(0);
      const decision = this.classify(error);
      this.events.onDone({
        state: "failed",
        error:
          decision.kind === "fail" ? decision.message : "Upload could not be started; use Retry",
        code: decision.kind === "fail" ? decision.code : "network",
      });
    }
  }

  private stopped(offset: number): void {
    this.events.onDone(
      this.stopReason === CANCEL ? { state: "cancelled" } : { state: "paused", offset },
    );
  }

  private terminalOutcome(session: UploadSession): RunnerOutcome | null {
    switch (session.status) {
      case "completed":
        return { state: "completed", session };
      case "duplicate":
        return { state: "duplicate", session };
      case "failed":
      case "expired":
        return { state: "failed", session };
      case "aborted":
        return { state: "cancelled" };
      default:
        return null;
    }
  }

  private classify(
    error: unknown,
  ):
    | { kind: "resync"; offset: number | null }
    | { kind: "retry" }
    | { kind: "fail"; message: string; code: string | null } {
    if (error instanceof ApiUnreachableError) return { kind: "retry" };
    if (error instanceof ApiError) {
      const code = error.code === "http_error" ? null : error.code;
      if (code === "upload_offset_mismatch") {
        const details = error.details as { committed_offset?: number } | undefined;
        return { kind: "resync", offset: details?.committed_offset ?? null };
      }
      if (RETRYABLE_HTTP.has(error.status)) return { kind: "retry" };
      return { kind: "fail", message: friendlyMessage(code, error.message), code };
    }
    return { kind: "retry" };
  }
}

export function friendlyMessage(code: string | null, fallback: string): string {
  switch (code) {
    case "upload_gone":
      return "This upload session expired or was cancelled; start it again";
    case "upload_forbidden":
      return "You are not allowed to upload for this production owner";
    case "upload_quota_exceeded":
      return "Too many uploads in progress; it will start when a slot frees up";
    case "upload_invalid_file":
      return "This file type or size is not accepted";
    case "upload_invalid_taxonomy":
      return "Choose a valid product category and content type";
    case "upload_invalid_revision":
      return "Only a rejected final can be replaced";
    case "upload_chunk_digest_mismatch":
      return "A chunk was corrupted in transit; retrying";
    case "upload_storage_unavailable":
      return "Uploads are not enabled on this server";
    default:
      return fallback.replace(/[\x00-\x1f\x7f]/g, "").slice(0, 200);
  }
}
