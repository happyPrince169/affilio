import type { UploadSession } from "@affilio/shared-types";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/uploads/sha256", () => ({
  sha256Hex: async (blob: Blob) => `sha-${new Uint8Array(await blob.arrayBuffer()).join("")}`,
}));

import { matchesSession, verifyResumeIdentity } from "@/lib/uploads/file-identity";
import { STORAGE_KEY, forgetSession, loadStored, saveStored } from "@/lib/uploads/persistence";

const session = {
  id: "s1",
  expected_size: 6,
  committed_offset: 4,
  last_chunk_offset: 2,
  last_chunk_sha256: "sha-34",
} as unknown as UploadSession;

describe("reselected file identity", () => {
  it("matches by name, size and last-modified when known", () => {
    const f = new File([new Uint8Array(6)], "a.mp4", { lastModified: 1000 });
    expect(matchesSession(f, { uploadId: "s1", name: "a.mp4", size: 6, lastModified: 1000 })).toBe(
      true,
    );
    expect(matchesSession(f, { uploadId: "s1", name: "a.mp4", size: 6, lastModified: null })).toBe(
      true,
    );
    expect(matchesSession(f, { uploadId: "s1", name: "a.mp4", size: 6, lastModified: 999 })).toBe(
      false,
    );
    expect(matchesSession(f, { uploadId: "s1", name: "b.mp4", size: 6, lastModified: 1000 })).toBe(
      false,
    );
    expect(matchesSession(f, { uploadId: "s1", name: "a.mp4", size: 7, lastModified: 1000 })).toBe(
      false,
    );
  });

  it("accepts the original file by hashing the last committed chunk, refuses another file", async () => {
    const original = new File([new Uint8Array([1, 2, 3, 4, 5, 6])], "a.mp4");
    expect(await verifyResumeIdentity(original, session)).toEqual({ ok: true, offset: 4 });
    const impostor = new File([new Uint8Array([1, 2, 9, 9, 5, 6])], "a.mp4"); // same name and size
    expect(await verifyResumeIdentity(impostor, session)).toEqual({
      ok: false,
      reason: "chunk_mismatch",
    });
    const shorter = new File([new Uint8Array(5)], "a.mp4");
    expect(await verifyResumeIdentity(shorter, session)).toEqual({
      ok: false,
      reason: "size_mismatch",
    });
    const fresh = {
      ...session,
      committed_offset: 0,
      last_chunk_offset: null,
      last_chunk_sha256: null,
    } as UploadSession;
    expect(await verifyResumeIdentity(original, fresh)).toEqual({ ok: true, offset: 0 });
  });
});

describe("persistence", () => {
  beforeEach(() => localStorage.clear());

  it("stores only non-sensitive metadata and drops malformed entries", () => {
    saveStored({
      batch: { batchId: "b", ownerId: "o", productCategoryId: "p", contentTypeId: "c" },
      sessions: [
        { uploadId: "s1", name: "a.mp4", size: 6, lastModified: 1000 },
        { uploadId: "s2", name: "b.mp4", size: 7, lastModified: null },
      ],
    });
    const raw = localStorage.getItem(STORAGE_KEY) as string;
    expect(raw).not.toMatch(/cookie|token|password|\/volume1|bytes/i);
    expect(loadStored().sessions.map((s) => s.uploadId)).toEqual(["s1", "s2"]);
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        sessions: [
          { uploadId: 1 },
          "junk",
          { uploadId: "ok", name: "n", size: 1, lastModified: null },
        ],
      }),
    );
    expect(loadStored().sessions.map((s) => s.uploadId)).toEqual(["ok"]);
    localStorage.setItem(STORAGE_KEY, "{not json");
    expect(loadStored()).toEqual({ batch: null, sessions: [] });
  });

  it("forgets a session once it is terminal", () => {
    saveStored({
      batch: null,
      sessions: [{ uploadId: "s1", name: "a.mp4", size: 1, lastModified: null }],
    });
    forgetSession("s1");
    expect(loadStored().sessions).toEqual([]);
  });
});
