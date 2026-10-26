# syntax=docker/dockerfile:1.7
# AFFILIO API + worker image. One image, two commands (see infrastructure/docker/synology).
#
# Build args:
#   APP_UID / APP_GID  – uid/gid the service runs as. Must be able to READ the originals and
#                        proxies bind mounts and WRITE thumbnails/exports. Synology's first
#                        admin user is usually uid 1026, group "users" is gid 100.

FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini curl \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

FROM base AS builder
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
# Dependencies first (cached layer), then the project itself.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM base AS runtime
ARG APP_UID=1026
ARG APP_GID=100
RUN (getent group "${APP_GID}" >/dev/null || groupadd -g "${APP_GID}" affilio) \
 && useradd -u "${APP_UID}" -g "${APP_GID}" -m -s /usr/sbin/nologin affilio \
 && mkdir -p /app /media/originals /media/proxies /media/finals /data/thumbnails /data/exports \
 && chown -R "${APP_UID}:${APP_GID}" /app /data
WORKDIR /app
COPY --from=builder --chown=${APP_UID}:${APP_GID} /opt/venv /opt/venv
COPY --from=builder --chown=${APP_UID}:${APP_GID} /app /app
COPY --chown=${APP_UID}:${APP_GID} docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENV PATH="/opt/venv/bin:${PATH}" \
    APP_ENV=production \
    LOG_FORMAT=json \
    MEDIA_ORIGINALS_PATH=/media/originals \
    MEDIA_PROXIES_PATH=/media/proxies \
    MEDIA_FINALS_PATH=/media/finals \
    MEDIA_THUMBNAILS_PATH=/data/thumbnails \
    MEDIA_EXPORTS_PATH=/data/exports \
    WORKER_HEARTBEAT_PATH=/tmp/affilio-worker-heartbeat
USER affilio
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["api"]
