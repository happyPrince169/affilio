import { sha256Hex } from "@/lib/uploads/sha256";

/**
 * Reload recovery (upload track U5, hardened in U7): a browser cannot keep `File` objects
 * across reloads, so the user reselects files and the client must prove a reselected file
 * is the one the server session belongs to before sending more bytes.
 *
 * Two layers:
 * 1. `matchesSession`: cheap metadata match (name, size, last-modified when known).
 * 2. `verifyResumeIdentity`: the server keeps the SHA-256 of every committed chunk
 *    (`/manifest`). The client hashes the reselected file chunk by chunk over the whole
 *    committed prefix, one chunk in memory at a time, and every digest must match. A file
 *    that differs anywhere in the already-uploaded part is refused, so the bytes appended
 *    afterwards always continue the same file. With nothing committed there is nothing to
 *    prove: every byte will come from the selected file and be verified per chunk.
 */

export interface StoredSessionMeta {
  uploadId: string;
  name: string;
  size: number;
  lastModified: number | null;
}

export function matchesSession(file: File, meta: StoredSessionMeta): boolean {
  if (file.name !== meta.name || file.size !== meta.size) return false;
  return meta.lastModified === null || file.lastModified === meta.lastModified;
}

export type IdentityResult =
  | { ok: true; offset: number; verifiedChunks: number }
  | { ok: false; reason: "size_mismatch" | "chunk_mismatch" | "manifest_incomplete" };

export interface ResumeManifest {
  chunk_bytes: number;
  expected_size: number;
  committed_offset: number;
  digests: string[];
}

export async function verifyResumeIdentity(
  file: File,
  manifest: ResumeManifest,
  onProgress?: (verifiedBytes: number) => void,
): Promise<IdentityResult> {
  if (file.size !== manifest.expected_size) return { ok: false, reason: "size_mismatch" };
  const committed = manifest.committed_offset;
  if (committed === 0) return { ok: true, offset: 0, verifiedChunks: 0 };
  const chunk = manifest.chunk_bytes;
  const expectedChunks = Math.ceil(committed / chunk);
  if (chunk <= 0 || manifest.digests.length < expectedChunks) {
    return { ok: false, reason: "manifest_incomplete" };
  }
  for (let index = 0; index < expectedChunks; index += 1) {
    const start = index * chunk;
    const end = Math.min(start + chunk, committed);
    const digest = await sha256Hex(file.slice(start, end));
    if (digest !== manifest.digests[index]?.toLowerCase()) {
      return { ok: false, reason: "chunk_mismatch" };
    }
    onProgress?.(end);
  }
  return { ok: true, offset: committed, verifiedChunks: expectedChunks };
}
