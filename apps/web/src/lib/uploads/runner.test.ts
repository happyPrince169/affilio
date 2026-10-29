import type { UploadSession } from "@affilio/shared-types";
import { describe, expect, it, vi } from "vitest";

import { ApiError, ApiUnreachableError } from "@/lib/api/client";
import { ItemRunner, type RunnerApi, type RunnerOutcome } from "@/lib/uploads/runner";

const CHUNK = 4;

function session(patch: Partial<UploadSession> = {}): UploadSession {
  return {
    id: "s1",
    status: "created",
    original_filename: "a.mp4",
    expected_size: 10,
    chunk_bytes: CHUNK,
    committed_offset: 0,
    production_owner: { id: "o", slug: "o", name: "O" },
    production_owner_name: "O",
    product_category: { id: "p", slug: "p", name: "P" },
    content_type: { id: "c", slug: "c", name: "C" },
    batch_id: null,
    replaces_asset_id: null,
    uploaded_by_user_id: "u",
    submitted_for_user_id: null,
    media_asset_id: null,
    duplicate_of_asset_id: null,
    failure_code: null,
    failure_detail: null,
    finalize_attempts: 0,
    review: null,
    last_chunk_offset: null,
    last_chunk_sha256: null,
    asset_title: null,
    thumbnail_url: null,
    created_at: "",
    updated_at: "",
    uploaded_at: null,
    completed_at: null,
    expires_at: "",
    ...patch,
  };
}

/** In-memory server: strict offsets, idempotent replay, records every chunk digest. */
function fakeServer(initial = session()) {
  let committed = initial.committed_offset;
  let lastOffset: number | null = null;
  let lastDigest: string | null = null;
  const calls: Array<{ offset: number; digest: string; size: number }> = [];
  const failures: Array<() => Error> = [];
  const api: RunnerApi = {
    createUpload: vi.fn(async () => initial),
    fetchUpload: vi.fn(async () => ({
      ...initial,
      committed_offset: committed,
      status: initial.status === "created" ? ("uploading" as const) : initial.status,
    })),
    uploadChunk: vi.fn(async (_id, offset, chunk, digest) => {
      const fail = failures.shift();
      if (fail) throw fail();
      if (offset === lastOffset && offset + chunk.size === committed && digest === lastDigest) {
        return {
          id: "s1",
          status: "uploading" as const,
          committed_offset: committed,
          expected_size: 10,
          replayed: true,
        };
      }
      if (offset !== committed) {
        throw new ApiError(
          409,
          {
            code: "upload_offset_mismatch",
            message: "",
            details: { committed_offset: committed },
            request_id: null,
          },
          "409",
        );
      }
      calls.push({ offset, digest, size: chunk.size });
      lastOffset = offset;
      lastDigest = digest;
      committed += chunk.size;
      return {
        id: "s1",
        status: "uploading" as const,
        committed_offset: committed,
        expected_size: 10,
        replayed: false,
      };
    }),
    completeUpload: vi.fn(async () => ({
      ...initial,
      status: "uploaded" as const,
      committed_offset: committed,
    })),
  };
  return {
    api,
    calls,
    failures,
    get committed() {
      return committed;
    },
  };
}

const file = () => new File([new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])], "a.mp4");
const hash = async (blob: Blob) =>
  `h${blob.size}-${new Uint8Array(await blob.arrayBuffer()).join("")}`;
const noSleep = async () => {};

async function runToEnd(server: ReturnType<typeof fakeServer>, uploadId: string | null = null) {
  const outcomes: RunnerOutcome[] = [];
  const progress: number[] = [];
  const runner = new ItemRunner(
    file(),
    server.api,
    {
      onSession: () => {},
      onProgress: (o) => progress.push(o),
      onDone: (o) => outcomes.push(o),
    },
    { sleep: noSleep, hash },
  );
  await runner.run(
    uploadId,
    uploadId
      ? null
      : {
          filename: "a.mp4",
          size: 10,
          production_owner_category_id: "o",
          product_category_id: "p",
          content_type_id: "c",
        },
  );
  return { runner, outcomes, progress };
}

describe("ItemRunner", () => {
  it("creates a session, sends sequential chunks with their digests and completes", async () => {
    const server = fakeServer();
    const { outcomes, progress } = await runToEnd(server);
    expect(server.calls.map((c) => [c.offset, c.size])).toEqual([
      [0, 4],
      [4, 4],
      [8, 2],
    ]);
    expect(server.calls[0]?.digest).toBe("h4-1234");
    expect(progress).toEqual([0, 4, 8, 10]);
    expect(server.api.completeUpload).toHaveBeenCalledWith("s1");
    expect(outcomes).toEqual([
      { state: "processing", session: expect.objectContaining({ status: "uploaded" }) },
    ]);
  });

  it("resumes from the server offset and re-syncs after an offset mismatch", async () => {
    const server = fakeServer(session({ committed_offset: 4, status: "uploading" }));
    const { outcomes } = await runToEnd(server, "s1");
    expect(server.api.createUpload).not.toHaveBeenCalled();
    expect(server.calls.map((c) => c.offset)).toEqual([4, 8]);
    expect(outcomes[0]?.state).toBe("processing");
  });

  it("retries transient failures with a bound and reports a failure afterwards", async () => {
    const server = fakeServer();
    server.failures.push(
      () => new ApiUnreachableError("x", new Error("net")),
      () => new ApiError(503, null, "503"),
    );
    const first = await runToEnd(server);
    expect(first.outcomes[0]?.state).toBe("processing"); // two transient errors, then success
    expect(server.calls.length).toBe(3);

    const stubborn = fakeServer();
    for (let i = 0; i < 5; i += 1)
      stubborn.failures.push(() => new ApiUnreachableError("x", new Error("net")));
    const second = await runToEnd(stubborn);
    expect(second.outcomes[0]).toMatchObject({ state: "failed", code: "retries_exhausted" });
  });

  it("stops on permanent errors with a friendly message", async () => {
    const server = fakeServer();
    server.failures.push(
      () =>
        new ApiError(
          410,
          { code: "upload_gone", message: "", details: null, request_id: null },
          "410",
        ),
    );
    const { outcomes } = await runToEnd(server);
    expect(outcomes[0]).toMatchObject({ state: "failed", code: "upload_gone" });
    expect((outcomes[0] as { error: string }).error).toMatch(/expired or was cancelled/);
  });

  it("pauses between chunks and keeps the server offset; cancel reports cancelled", async () => {
    const server = fakeServer();
    const outcomes: RunnerOutcome[] = [];
    const runner = new ItemRunner(
      file(),
      server.api,
      {
        onSession: () => {},
        onProgress: (o) => {
          if (o === 4) runner.pause();
        },
        onDone: (o) => outcomes.push(o),
      },
      { sleep: noSleep, hash },
    );
    await runner.run(null, {
      filename: "a.mp4",
      size: 10,
      production_owner_category_id: "o",
      product_category_id: "p",
      content_type_id: "c",
    });
    expect(outcomes).toEqual([{ state: "paused", offset: 4 }]);
    expect(server.committed).toBe(4);

    const cancelled: RunnerOutcome[] = [];
    const second = new ItemRunner(
      file(),
      server.api,
      {
        onSession: () => second.cancel(),
        onProgress: () => {},
        onDone: (o) => cancelled.push(o),
      },
      { sleep: noSleep, hash },
    );
    await second.run("s1", null);
    expect(cancelled).toEqual([{ state: "cancelled" }]);
  });

  it("treats a session the worker already finished as its terminal outcome", async () => {
    const server = fakeServer(session({ status: "duplicate" }));
    const { outcomes } = await runToEnd(server, "s1");
    expect(outcomes[0]?.state).toBe("duplicate");
    expect(server.api.uploadChunk).not.toHaveBeenCalled();
  });
});
