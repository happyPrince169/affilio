import type { UploadSession } from "@affilio/shared-types";

import { sha256Hex } from "@/lib/uploads/sha256";

/**
 * Reload recovery (upload track U5): a browser cannot keep `File` objects across reloads,
 * so the user reselects files and the client must prove a reselected file is the one the
 * server session belongs to before sending more bytes.
 *
 * Two layers:
 * 1. `matchesSession`: cheap metadata match (name, size, last-modified when known).
 * 2. `verifyResumeIdentity`: hash the local bytes over [last_chunk_offset, committed_offset)
 *    and compare with the server's `last_chunk_sha256`. The server recorded that digest when
 *    it committed the chunk, so a different file with the same name and size is refused and
 *    no byte from the wrong file is ever appended.
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
  { ok: true; offset: number } | { ok: false; reason: "size_mismatch" | "chunk_mismatch" };

export async function verifyResumeIdentity(
  file: File,
  session: UploadSession,
): Promise<IdentityResult> {
  if (file.size !== session.expected_size) return { ok: false, reason: "size_mismatch" };
  const committed = session.committed_offset;
  if (committed === 0 || session.last_chunk_offset === null || !session.last_chunk_sha256) {
    return { ok: true, offset: committed };
  }
  const slice = file.slice(session.last_chunk_offset, committed);
  const digest = await sha256Hex(slice);
  if (digest !== session.last_chunk_sha256.toLowerCase()) {
    return { ok: false, reason: "chunk_mismatch" };
  }
  return { ok: true, offset: committed };
}
