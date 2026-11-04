import type { UploadOptions } from "@affilio/shared-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { StrictMode, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type * as UploadsApi from "@/lib/api/uploads";

vi.mock("@/lib/api/uploads", async (importOriginal) => {
  const actual = await importOriginal<typeof UploadsApi>();
  return {
    ...actual,
    fetchUploads: vi.fn(async () => []),
    fetchUploadManifest: vi.fn(),
    createUpload: vi.fn(),
    fetchUpload: vi.fn(),
    uploadChunk: vi.fn(),
    completeUpload: vi.fn(),
    abortUpload: vi.fn(),
  };
});

import { useUploadQueue } from "@/components/uploads/use-upload-queue";

const options: UploadOptions = {
  facets_enabled: true,
  can_submit_for_others: false,
  owners: [{ id: "o", slug: "hang", name: "Hang" }],
  product_categories: [{ id: "p", slug: "mens", name: "Men's Fashion" }],
  content_types: [{ id: "c", slug: "review", name: "Review" }],
  allowed_extensions: [".mp4"],
  limits: {
    chunk_bytes: 8,
    max_file_bytes: 100,
    max_active_sessions_per_user: 20,
    session_ttl_hours: 48,
    default_concurrency: 2,
  },
};

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient();
  return (
    <StrictMode>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </StrictMode>
  );
}

describe("useUploadQueue.addFiles", () => {
  it("adds each selected file exactly once, even when React double-invokes updaters", async () => {
    // Batch defaults are left empty so nothing starts uploading; the hook never gets options
    // here either, which keeps the scheduler idle and the test about the queue only.
    const { result } = renderHook(() => useUploadQueue(undefined), { wrapper });
    await waitFor(() => expect(result.current.restoring).toBe(false));
    const clip = new File([new Uint8Array(32)], "clip.mp4", { lastModified: 1 });
    const other = new File([new Uint8Array(16)], "other.mp4", { lastModified: 2 });
    let outcome: ReturnType<typeof result.current.addFiles> | undefined;
    act(() => {
      outcome = result.current.addFiles([clip, other, new File([new Uint8Array(4)], "notes.txt")]);
    });
    expect(outcome).toEqual({ added: 2, resumed: 0, skipped: 1, mismatched: 0 });
    expect(result.current.items.map((i) => i.name)).toEqual(["clip.mp4", "other.mp4"]);
    act(() => {
      result.current.addFiles([new File([new Uint8Array(8)], "third.mp4")]);
    });
    expect(result.current.items).toHaveLength(3);
    expect(new Set(result.current.items.map((i) => i.localId)).size).toBe(3);
  });

  it("keeps the options-driven scheduler idle without batch defaults", async () => {
    const { result } = renderHook(() => useUploadQueue(options), { wrapper });
    await waitFor(() => expect(result.current.restoring).toBe(false));
    act(() => {
      result.current.addFiles([new File([new Uint8Array(32)], "clip.mp4")]);
    });
    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0]?.state).toBe("queued");
  });
});
