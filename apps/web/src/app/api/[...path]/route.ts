/**
 * Same-origin API proxy.
 *
 * The browser only talks to the web container; this handler forwards `/api/*` to the FastAPI
 * service on the internal Docker network so the API never needs a published port.
 *
 * Security (M3A): the proxy is identity-neutral. It forwards the browser's session cookie and
 * origin headers unchanged and never attaches a service token, so an unauthenticated browser
 * request stays unauthenticated at the API. `Set-Cookie` from login/logout is passed back as is.
 */

const API_INTERNAL_URL = (process.env.API_INTERNAL_URL ?? "http://localhost:8000").replace(
  /\/+$/,
  "",
);
const FORWARD_REQUEST_HEADERS = [
  "accept",
  "content-type",
  "cookie",
  "origin",
  "sec-fetch-site",
  "user-agent",
  "x-organization-slug",
  "x-request-id",
];
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
  // Lets the API compare `Origin` against the host the browser actually used (CSRF check).
  headers.set("x-forwarded-host", request.headers.get("host") ?? incoming.host);
  headers.set("x-forwarded-proto", incoming.protocol.replace(":", ""));
  // Client address for API rate limiting. Next.js populates x-forwarded-for from the socket
  // when no upstream proxy set it; the API only trusts this with TRUST_PROXY_HEADERS=true.
  const forwardedFor = request.headers.get("x-forwarded-for");
  if (forwardedFor) headers.set("x-forwarded-for", forwardedFor);

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
  for (const cookie of upstream.headers.getSetCookie()) {
    responseHeaders.append("set-cookie", cookie);
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
