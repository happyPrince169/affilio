import type { StoredSessionMeta } from "@/lib/uploads/file-identity";

/**
 * What survives a reload (upload track U5): only non-sensitive queue metadata so the page
 * can list the server sessions that still need their local file. No bytes, no cookies, no
 * tokens, no filesystem paths: the session id is meaningless without the HttpOnly cookie,
 * and progress is always re-read from the server, never from this store.
 */

export const STORAGE_KEY = "affilio.uploads.v1";
const MAX_ENTRIES = 200;

export interface StoredBatch {
  batchId: string;
  ownerId: string | null;
  productCategoryId: string | null;
  contentTypeId: string | null;
}

export interface StoredState {
  batch: StoredBatch | null;
  sessions: StoredSessionMeta[];
}

const EMPTY: StoredState = { batch: null, sessions: [] };

export function loadStored(storage: Storage | null = safeStorage()): StoredState {
  if (!storage) return EMPTY;
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return EMPTY;
    const parsed = JSON.parse(raw) as Partial<StoredState>;
    const sessions = Array.isArray(parsed.sessions)
      ? parsed.sessions.filter(isSessionMeta).slice(0, MAX_ENTRIES)
      : [];
    const batch = parsed.batch && typeof parsed.batch.batchId === "string" ? parsed.batch : null;
    return { batch, sessions };
  } catch {
    return EMPTY;
  }
}

export function saveStored(state: StoredState, storage: Storage | null = safeStorage()): void {
  if (!storage) return;
  try {
    const sessions = state.sessions.filter(isSessionMeta).slice(-MAX_ENTRIES);
    storage.setItem(STORAGE_KEY, JSON.stringify({ batch: state.batch, sessions }));
  } catch {
    // Private mode or quota: the server list still restores sessions, only the
    // last-modified hint is lost.
  }
}

export function forgetSession(uploadId: string, storage: Storage | null = safeStorage()): void {
  const state = loadStored(storage);
  saveStored(
    { ...state, sessions: state.sessions.filter((s) => s.uploadId !== uploadId) },
    storage,
  );
}

function isSessionMeta(value: unknown): value is StoredSessionMeta {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.uploadId === "string" &&
    typeof v.name === "string" &&
    typeof v.size === "number" &&
    (v.lastModified === null || typeof v.lastModified === "number")
  );
}

function safeStorage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}
