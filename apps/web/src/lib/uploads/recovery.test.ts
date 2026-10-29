import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/uploads/sha256", () => ({
  sha256Hex: async (blob: Blob) => `sha-${new Uint8Array(await blob.arrayBuffer()).join("")}`,
}));

import { matchesSession, verifyResumeIdentity } from "@/lib/uploads/file-identity";
import { STORAGE_KEY, forgetSession, loadStored, saveStored } from "@/lib/uploads/persistence";

const CHUNK = 2;
const digest = (bytes: number[]) => `sha-${bytes.join("")}`;
/** Manifest of a 6-byte file [1,2,3,4,5,6] with 4 bytes committed in 2-byte chunks. */
const manifest = {
  chunk_bytes: CHUNK,
  expected_size: 6,
  committed_offset: 4,
  digests: [digest([1, 2]), digest([3, 4])],
};

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

  it("accepts the original file after hashing every committed chunk", async () => {
    const original = new File([new Uint8Array([1, 2, 3, 4, 5, 6])], "a.mp4");
    const seen: number[] = [];
    expect(await verifyResumeIdentity(original, manifest, (n) => seen.push(n))).toEqual({
      ok: true,
      offset: 4,
      verifiedChunks: 2,
    });
    expect(seen).toEqual([2, 4]);
  });

  it("refuses a same-metadata file that differs in an early chunk even when the last chunk matches", async () => {
    // Bytes 0-1 differ, bytes 2-3 (the last committed chunk) are identical: the U5 check
    // would have accepted this file; the whole-prefix check refuses it.
    const impostor = new File([new Uint8Array([9, 9, 3, 4, 5, 6])], "a.mp4");
    expect(await verifyResumeIdentity(impostor, manifest)).toEqual({
      ok: false,
      reason: "chunk_mismatch",
    });
    const lastDiffers = new File([new Uint8Array([1, 2, 3, 9, 5, 6])], "a.mp4");
    expect(await verifyResumeIdentity(lastDiffers, manifest)).toEqual({
      ok: false,
      reason: "chunk_mismatch",
    });
    // A file that differs only beyond the committed offset is the same committed prefix: the
    // remaining bytes come from it and are verified chunk by chunk on upload.
    const tailDiffers = new File([new Uint8Array([1, 2, 3, 4, 8, 8])], "a.mp4");
    expect((await verifyResumeIdentity(tailDiffers, manifest)).ok).toBe(true);
  });

  it("handles size mismatch, empty sessions and incomplete manifests", async () => {
    const original = new File([new Uint8Array([1, 2, 3, 4, 5, 6])], "a.mp4");
    expect(await verifyResumeIdentity(new File([new Uint8Array(5)], "a.mp4"), manifest)).toEqual({
      ok: false,
      reason: "size_mismatch",
    });
    expect(
      await verifyResumeIdentity(original, { ...manifest, committed_offset: 0, digests: [] }),
    ).toEqual({
      ok: true,
      offset: 0,
      verifiedChunks: 0,
    });
    expect(
      await verifyResumeIdentity(original, { ...manifest, digests: [digest([1, 2])] }),
    ).toEqual({
      ok: false,
      reason: "manifest_incomplete",
    });
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
