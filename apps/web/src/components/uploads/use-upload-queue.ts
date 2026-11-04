"use client";

import type { UploadCreate, UploadOptions, UploadSession } from "@affilio/shared-types";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  abortUpload,
  completeUpload,
  createUpload,
  fetchUpload,
  fetchUploadManifest,
  fetchUploads,
  uploadChunk,
  uploadQueries,
} from "@/lib/api/uploads";
import { matchesSession, verifyResumeIdentity } from "@/lib/uploads/file-identity";
import { forgetSession, loadStored, saveStored } from "@/lib/uploads/persistence";
import {
  type BatchDefaults,
  type Classification,
  DEFAULT_CONCURRENCY,
  type QueueItem,
  SLOT_STATES,
  TERMINAL_STATES,
  effectiveClassification,
  isVideoFile,
  itemFromFile,
  itemFromSession,
  pickStartable,
} from "@/lib/uploads/queue";
import { ItemRunner, friendlyMessage } from "@/lib/uploads/runner";

const POLL_MS = 3_000;
const POLL_LIMIT = 400; // ~20 minutes per item before the page stops asking
const ACTIVE_SERVER_STATUSES = [
  "created",
  "uploading",
  "uploaded",
  "verifying",
  "finalizing",
] as const;

export interface CorrectionContext {
  replacesAssetId: string;
  title: string;
  ownerId: string;
  productCategoryId: string;
  contentTypeId: string;
}

export interface UseUploadQueueResult {
  items: QueueItem[];
  defaults: BatchDefaults;
  setDefaults: (patch: Partial<BatchDefaults>) => void;
  concurrency: number;
  setConcurrency: (n: number) => void;
  addFiles: (files: File[]) => {
    added: number;
    resumed: number;
    skipped: number;
    mismatched: number;
  };
  setOverride: (localId: string, override: Classification) => void;
  pause: (localId: string) => void;
  resume: (localId: string) => void;
  cancel: (localId: string) => void;
  retry: (localId: string) => void;
  remove: (localId: string) => void;
  clearFinished: () => void;
  restoring: boolean;
  needsFileCount: number;
  correction: CorrectionContext | null;
  startCorrection: (ctx: CorrectionContext) => void;
  clearCorrection: () => void;
}

function newBatchId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function useUploadQueue(options: UploadOptions | undefined): UseUploadQueueResult {
  const queryClient = useQueryClient();
  const [items, setItems] = useState<QueueItem[]>([]);
  const [defaults, setDefaultsState] = useState<BatchDefaults>(() => {
    const stored = loadStored();
    return {
      ownerId: stored.batch?.ownerId ?? null,
      productCategoryId: stored.batch?.productCategoryId ?? null,
      contentTypeId: stored.batch?.contentTypeId ?? null,
      submittedForUserId: null,
      batchId: stored.batch?.batchId ?? newBatchId(),
    };
  });
  const [concurrency, setConcurrency] = useState(DEFAULT_CONCURRENCY);
  const [restoring, setRestoring] = useState(true);
  const [correction, setCorrection] = useState<CorrectionContext | null>(null);
  const runners = useRef(new Map<string, ItemRunner>());
  const sessionsRef = useRef(new Map<string, UploadSession>());
  const pollCounts = useRef(new Map<string, number>());

  const patch = useCallback(
    (localId: string, update: Partial<QueueItem> | ((i: QueueItem) => Partial<QueueItem>)) => {
      setItems((prev) =>
        prev.map((i) =>
          i.localId === localId
            ? { ...i, ...(typeof update === "function" ? update(i) : update) }
            : i,
        ),
      );
    },
    [],
  );

  // ---- restore sessions from the server after a reload (metadata hints from localStorage)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const stored = loadStored();
        const hints = new Map(stored.sessions.map((s) => [s.uploadId, s]));
        const lists = await Promise.all(
          ACTIVE_SERVER_STATUSES.map((status) => fetchUploads({ status, limit: 200 })),
        );
        if (cancelled) return;
        const restored = lists.flat().map((session) => {
          sessionsRef.current.set(session.id, session);
          const item = itemFromSession(session);
          const hint = hints.get(session.id);
          return hint ? { ...item, lastModified: hint.lastModified } : item;
        });
        setItems((prev) => {
          const known = new Set(prev.map((i) => i.uploadId));
          return [...prev, ...restored.filter((i) => !known.has(i.uploadId))];
        });
      } finally {
        if (!cancelled) setRestoring(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // ---- persist non-sensitive metadata of sessions that may need a reselected file
  useEffect(() => {
    saveStored({
      batch: {
        batchId: defaults.batchId,
        ownerId: defaults.ownerId,
        productCategoryId: defaults.productCategoryId,
        contentTypeId: defaults.contentTypeId,
      },
      sessions: items
        .filter((i) => i.uploadId && !TERMINAL_STATES.has(i.state))
        .map((i) => ({
          uploadId: i.uploadId as string,
          name: i.name,
          size: i.size,
          lastModified: i.lastModified,
        })),
    });
  }, [items, defaults]);

  const applySession = useCallback(
    (localId: string, session: UploadSession) => {
      sessionsRef.current.set(session.id, session);
      const state =
        session.status === "completed"
          ? "completed"
          : session.status === "duplicate"
            ? "duplicate"
            : session.status === "aborted"
              ? "cancelled"
              : ["failed", "expired"].includes(session.status)
                ? "failed"
                : "processing";
      patch(localId, {
        state,
        serverStatus: session.status,
        mediaAssetId: session.media_asset_id,
        offset: session.committed_offset,
        error:
          state === "failed"
            ? friendlyMessage(session.failure_code, session.failure_detail ?? "Upload failed")
            : state === "duplicate"
              ? "Identical content is already in the library; nothing was added"
              : null,
      });
      if (TERMINAL_STATES.has(state)) {
        if (session.id) forgetSession(session.id);
        void queryClient.invalidateQueries({ queryKey: uploadQueries.all() });
      }
    },
    [patch, queryClient],
  );

  // ---- scheduler: start queued items while slots are free
  useEffect(() => {
    if (!options) return;
    const startable = pickStartable(
      items,
      defaults,
      concurrency,
      options.limits.max_active_sessions_per_user,
    );
    for (const item of startable) {
      if (runners.current.has(item.localId) || !item.file) continue;
      const cls = effectiveClassification(item, defaults);
      const body: UploadCreate | null = item.uploadId
        ? null
        : {
            filename: item.file.name,
            size: item.file.size,
            production_owner_category_id: defaults.ownerId as string,
            product_category_id: cls.productCategoryId as string,
            content_type_id: cls.contentTypeId as string,
            batch_id: defaults.batchId,
            replaces_asset_id: item.replacesAssetId,
            submitted_for_user_id: options.can_submit_for_others
              ? defaults.submittedForUserId
              : null,
          };
      const runner = new ItemRunner(
        item.file,
        { createUpload, fetchUpload, uploadChunk, completeUpload },
        {
          onSession: (session) => {
            sessionsRef.current.set(session.id, session);
            patch(item.localId, {
              uploadId: session.id,
              offset: session.committed_offset,
              serverStatus: session.status,
              state: "uploading",
            });
          },
          onProgress: (offset) => patch(item.localId, { offset }),
          onDone: (outcome) => {
            runners.current.delete(item.localId);
            switch (outcome.state) {
              case "processing":
                sessionsRef.current.set(outcome.session.id, outcome.session);
                patch(item.localId, {
                  state: "processing",
                  offset: outcome.session.expected_size,
                  serverStatus: outcome.session.status,
                  error: null,
                });
                break;
              case "paused":
                patch(item.localId, { state: "paused", offset: outcome.offset });
                break;
              case "cancelled":
                patch(item.localId, { state: "cancelled" });
                break;
              case "completed":
              case "duplicate":
                applySession(item.localId, outcome.session);
                break;
              case "failed":
                if ("session" in outcome) applySession(item.localId, outcome.session);
                else patch(item.localId, { state: "failed", error: outcome.error });
                break;
            }
          },
        },
      );
      runners.current.set(item.localId, runner);
      patch(item.localId, { state: item.uploadId ? "uploading" : "creating", error: null });
      void runner.run(item.uploadId, body);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, defaults, concurrency, options]);

  // ---- poll processing items until the worker reports an outcome (bounded)
  useEffect(() => {
    const processing = items.filter((i) => i.state === "processing" && i.uploadId);
    if (processing.length === 0) return;
    const timer = setInterval(async () => {
      for (const item of processing) {
        const id = item.uploadId as string;
        const count = (pollCounts.current.get(id) ?? 0) + 1;
        pollCounts.current.set(id, count);
        if (count > POLL_LIMIT) {
          patch(item.localId, { error: "Still processing; check My uploads later" });
          continue;
        }
        try {
          const session = await fetchUpload(id);
          if (
            session.status !== "uploaded" &&
            session.status !== "verifying" &&
            session.status !== "finalizing"
          ) {
            applySession(item.localId, session);
          }
        } catch {
          // transient; next tick retries
        }
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [items, patch, applySession]);

  // ---- actions
  const setDefaults = useCallback((update: Partial<BatchDefaults>) => {
    setDefaultsState((prev) => ({ ...prev, ...update }));
  }, []);

  const addFiles = useCallback(
    (files: File[]) => {
      const allowed = options?.allowed_extensions ?? [".mp4", ".mov"];
      let added = 0;
      let resumed = 0;
      let skipped = 0;
      let mismatched = 0;
      const fresh: QueueItem[] = [];
      const pendingVerifications: Array<Promise<void>> = [];
      // Decide everything against the rendered snapshot, then apply one pure update: a state
      // updater may be invoked twice by React, which used to add every file twice.
      const current = items;
      const claimed = new Set<string>();
      for (const file of files) {
        if (!isVideoFile(file.name, allowed)) {
          skipped += 1;
          continue;
        }
        const candidate = current.find(
          (i) =>
            i.state === "needs_file" &&
            i.uploadId &&
            !claimed.has(i.localId) &&
            matchesSession(file, {
              uploadId: i.uploadId,
              name: i.name,
              size: i.size,
              lastModified: i.lastModified,
            }),
        );
        if (candidate) {
          claimed.add(candidate.localId);
          const localId = candidate.localId;
          pendingVerifications.push(
            (async () => {
              const manifest = await fetchUploadManifest(candidate.uploadId as string);
              const identity = await verifyResumeIdentity(file, manifest);
              if (identity.ok) {
                resumed += 1;
                patch(localId, {
                  file,
                  lastModified: file.lastModified,
                  state: "queued",
                  offset: identity.offset,
                  error: null,
                });
              } else {
                mismatched += 1;
                patch(localId, {
                  error:
                    identity.reason === "chunk_mismatch"
                      ? "This is not the same file as the interrupted upload; select the original file"
                      : identity.reason === "size_mismatch"
                        ? "File size differs from the interrupted upload"
                        : "The server's record of this upload is incomplete; cancel it and upload again",
                });
              }
            })(),
          );
          continue;
        }
        const item = itemFromFile(file);
        if (correction) {
          item.replacesAssetId = correction.replacesAssetId;
          item.override = {
            productCategoryId: correction.productCategoryId,
            contentTypeId: correction.contentTypeId,
          };
        }
        fresh.push(item);
        added += 1;
      }
      if (fresh.length) setItems((prev) => [...prev, ...fresh]);
      if (correction && added > 0) setCorrection(null);
      void Promise.all(pendingVerifications);
      return { added, resumed, skipped, mismatched };
    },
    [items, options, correction, patch],
  );

  const setOverride = useCallback(
    (localId: string, override: Classification) => patch(localId, { override }),
    [patch],
  );

  const pause = useCallback((localId: string) => {
    runners.current.get(localId)?.pause();
  }, []);

  const resume = useCallback(
    (localId: string) =>
      patch(localId, (i) =>
        i.state === "paused" && i.file ? { state: "queued", error: null } : {},
      ),
    [patch],
  );

  const cancel = useCallback((localId: string) => {
    const runner = runners.current.get(localId);
    if (runner) runner.cancel();
    setItems((prev) =>
      prev.map((i) => {
        if (i.localId !== localId) return i;
        if (i.uploadId && SLOT_STATES.has(i.state)) {
          void abortUpload(i.uploadId).catch(() => undefined);
          forgetSession(i.uploadId);
        }
        return { ...i, state: "cancelled" };
      }),
    );
  }, []);

  const retry = useCallback(
    (localId: string) =>
      patch(localId, (i) =>
        i.state === "failed" && i.file
          ? {
              state: "queued",
              error: null,
              retries: 0,
              uploadId:
                i.serverStatus && ["created", "uploading"].includes(i.serverStatus)
                  ? i.uploadId
                  : null,
              offset: 0,
            }
          : {},
      ),
    [patch],
  );

  const remove = useCallback((localId: string) => {
    setItems((prev) => prev.filter((i) => i.localId !== localId || !TERMINAL_STATES.has(i.state)));
  }, []);

  const clearFinished = useCallback(() => {
    setItems((prev) => prev.filter((i) => !TERMINAL_STATES.has(i.state)));
  }, []);

  const needsFileCount = useMemo(
    () => items.filter((i) => i.state === "needs_file").length,
    [items],
  );

  return {
    items,
    defaults,
    setDefaults,
    concurrency,
    setConcurrency,
    addFiles,
    setOverride,
    pause,
    resume,
    cancel,
    retry,
    remove,
    clearFinished,
    restoring,
    needsFileCount,
    correction,
    startCorrection: setCorrection,
    clearCorrection: () => setCorrection(null),
  };
}
