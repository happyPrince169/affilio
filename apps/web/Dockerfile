# syntax=docker/dockerfile:1.7
# AFFILIO web image (Next.js standalone). Build from the REPOSITORY ROOT:
#   docker build -f apps/web/Dockerfile -t affilio-web .
#
# NEXT_PUBLIC_API_URL is inlined at build time. The production default is "" (same origin):
# the browser calls /api/* on this container and the route handler forwards to API_INTERNAL_URL.

FROM node:22-alpine AS base
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH NEXT_TELEMETRY_DISABLED=1
RUN corepack enable && apk add --no-cache libc6-compat

FROM base AS deps
WORKDIR /repo
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
COPY apps/web/package.json apps/web/package.json
COPY apps/api/package.json apps/api/package.json
COPY packages/config/package.json packages/config/package.json
COPY packages/shared-types/package.json packages/shared-types/package.json
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --filter @affilio/web... --filter @affilio/config

FROM deps AS builder
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
COPY packages ./packages
COPY apps/web ./apps/web
RUN pnpm --filter @affilio/web build

FROM node:22-alpine AS runner
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
ARG APP_UID=1026
ARG APP_GID=100
RUN apk add --no-cache curl \
 && (getent group "${APP_GID}" >/dev/null || addgroup -g "${APP_GID}" affilio) \
 && adduser -D -u "${APP_UID}" -G "$(getent group "${APP_GID}" | cut -d: -f1)" affilio
WORKDIR /app
COPY --from=builder --chown=${APP_UID}:${APP_GID} /repo/apps/web/.next/standalone ./
COPY --from=builder --chown=${APP_UID}:${APP_GID} /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=${APP_UID}:${APP_GID} /repo/apps/web/public ./apps/web/public
USER affilio
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:3000/ >/dev/null || exit 1
CMD ["node", "apps/web/server.js"]
