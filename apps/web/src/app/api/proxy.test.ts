import { afterEach, describe, expect, it, vi } from "vitest";

import { PATCH, GET } from "./[...path]/route";

/**
 * The upload proxy must stream request bodies (no whole-chunk buffering), forward the
 * upload headers, and abort upstream when the client disconnects.
 */
describe("API proxy streaming", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("passes the request body stream through with duplex half and the upload headers", async () => {
    const seen: { init?: RequestInit & { duplex?: string }; url?: string } = {};
    globalThis.fetch = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      seen.url = String(url);
      seen.init = init as RequestInit & { duplex?: string };
      // Drain the stream the way the API would, proving it is a live stream not a buffer.
      let bytes = 0;
      const reader = (init?.body as ReadableStream<Uint8Array>).getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        bytes += value.length;
      }
      return new Response(JSON.stringify({ committed_offset: bytes }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;

    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(3));
        controller.enqueue(new Uint8Array(5));
        controller.close();
      },
    });
    const request = new Request("http://web.local/api/v1/media/uploads/abc/chunk", {
      method: "PATCH",
      body,
      // @ts-expect-error duplex is required by undici for stream bodies
      duplex: "half",
      headers: {
        "upload-offset": "16",
        "x-upload-chunk-sha256": "f".repeat(64),
        "content-type": "application/offset+octet-stream",
        cookie: "affilio_session=secret",
        host: "web.local",
      },
    });
    const response = await PATCH(request, {
      params: Promise.resolve({ path: ["v1", "media", "uploads", "abc", "chunk"] }),
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ committed_offset: 8 });
    expect(seen.url).toBe("http://localhost:8000/api/v1/media/uploads/abc/chunk");
    expect(seen.init?.duplex).toBe("half");
    expect(seen.init?.body).toBeInstanceOf(ReadableStream);
    const headers = seen.init?.headers as Headers;
    expect(headers.get("upload-offset")).toBe("16");
    expect(headers.get("x-upload-chunk-sha256")).toBe("f".repeat(64));
    expect(headers.get("cookie")).toBe("affilio_session=secret");
    expect(headers.get("x-forwarded-host")).toBe("web.local");
  });

  it("sends no body for GET and forwards the abort signal", async () => {
    let signal: AbortSignal | undefined;
    globalThis.fetch = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      signal = init?.signal ?? undefined;
      return new Response("[]", { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    const request = new Request("http://web.local/api/v1/media/uploads", { method: "GET" });
    const response = await GET(request, {
      params: Promise.resolve({ path: ["v1", "media", "uploads"] }),
    });
    expect(response.status).toBe(200);
    const init = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0]?.[1] as RequestInit;
    expect(init.body).toBeUndefined();
    expect(signal).toBe(request.signal);
  });

  it("answers 499 when the client aborted the request", async () => {
    globalThis.fetch = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      const error = new Error("aborted");
      error.name = "AbortError";
      if (init?.signal?.aborted) throw error;
      throw error;
    }) as typeof fetch;
    const controller = new AbortController();
    const request = new Request("http://web.local/api/v1/x", {
      method: "POST",
      body: "{}",
      signal: controller.signal,
    });
    controller.abort();
    const response = await PATCH(request, { params: Promise.resolve({ path: ["v1", "x"] }) });
    expect(response.status).toBe(499);
  });
});
