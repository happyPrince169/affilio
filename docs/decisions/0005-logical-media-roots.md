# ADR 0005 — Logical media roots, read-only originals, single published port

- **Status:** Accepted (2026-10-26)
- **Milestone:** M1

## Context

Media lives on a Synology NAS that already runs a proxy-generation workflow. AFFILIO must
index it without ever modifying it, work both from an Ubuntu workstation (share mounted at an
arbitrary path) and inside containers on the NAS, and stay off the public internet until
authentication exists.

## Decision

1. **Logical roots.** Files are stored as `(media_root, relative_path)`. The four roots —
   `originals` (RO), `proxies` (RO), `thumbnails` (RW), `exports` (RW) — are bound to paths at
   runtime from settings (`MEDIA_*_PATH`). No absolute host path is ever persisted.
2. **Read-only by construction.** Originals and proxies are bind-mounted `read_only: true`, the
   scanner never opens them for writing, and `RootBinding.resolve()` refuses any path that
   escapes its root (including via symlinks). Thumbnails are written atomically inside the
   thumbnails root only.
3. **Proxies are variants, not assets.** The originals root is the source of truth; a proxy is
   linked by filename pattern and used for thumbnails. HEVC 10-bit proxies are explicitly not
   publish-ready; an H.264/AAC export pipeline is a later, separate concern (`exports` root).
4. **One published port.** Only the web container listens on the LAN. A Next.js route handler
   proxies `/api/*` to the API container and attaches the admin token server-side. PostgreSQL
   and the API have no published ports.

## Consequences

- The same database works unchanged when a share is remounted elsewhere.
- Path traversal is prevented at the resolver, not per endpoint.
- Thumbnail URLs are same-origin in production; the browser never needs CORS or a token.
- When authentication arrives (M3), the proxy route is where session cookies will be enforced.
