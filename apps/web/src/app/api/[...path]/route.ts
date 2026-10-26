/**
 * Same-origin API proxy.
 *
 * In production the browser only talks to the web container; this handler forwards
 * `/api/*` to the FastAPI service on the internal Docker network and attaches the
 * media admin token server-side, so the token never reaches the browser and the API
 * never needs a published port.
 */

const API_INTERNAL_URL = (process.env.API_INTERNAL_URL ?? "http://localhost:8000").replace(
  /\/+$/,
  "",
);
const ADMIN_TOKEN = process.env.MEDIA_ADMIN_TOKEN ?? "";
const FORWARD_REQUEST_HEADERS = ["accept", "content-type", "x-organization-slug", "x-request-id"];
const FORWARD_RESPONSE_HEADERS = [
  "content-type",
  "content-length",
  "cache-control",
  "etag",
  "last-modified",
  "x-request-id",
];

type Context = { params: Promise<{ path: string[] }> };

async function proxy(request: Request, ctx: Context): Promise<Response> {
  const { path } = await ctx.params;
  const incoming = new URL(request.url);
  const target = `${API_INTERNAL_URL}/api/${path.map(encodeURIComponent).join("/")}${incoming.search}`;

  const headers = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (ADMIN_TOKEN) headers.set("x-affilio-admin-token", ADMIN_TOKEN);

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return Response.json(
      {
        error: {
          code: "upstream_unreachable",
          message: "The AFFILIO API is not reachable from the web server",
          request_id: null,
        },
      },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers();
  for (const name of FORWARD_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
