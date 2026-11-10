#!/bin/sh
# Entrypoint for the API image.
#   api     – (default) run migrations when RUN_MIGRATIONS_ON_START=true, then uvicorn
#   worker  – background scan/thumbnail worker
#   publisher – Reels publisher worker (M3B.2A); reads only the finals mount
#   exporter  – publish-ready export worker (E1); reads finals, writes only the exports mount
#   migrate – apply migrations and exit
#   <cmd>   – anything else is exec'd verbatim (e.g. `python -m affilio.scripts.media_scan`)
set -eu

case "${1:-api}" in
  api)
    if [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
      echo "[entrypoint] applying database migrations"
      alembic upgrade head
    fi
    # X-Forwarded-* is honoured only from TRUSTED_PROXY_IPS (IPs or CIDR networks, comma
    # separated): the web proxy's network in production, loopback by default. Never "*":
    # any other peer that can reach the API (the MeoScout bridge on the ingest network)
    # must not be able to choose the client address or scheme the API records.
    exec uvicorn affilio.main:app \
      --host 0.0.0.0 --port 8000 \
      --workers "${UVICORN_WORKERS:-1}" \
      --no-access-log --proxy-headers --forwarded-allow-ips "${TRUSTED_PROXY_IPS:-127.0.0.1}"
    ;;
  worker)
    exec python -m affilio.worker
    ;;
  publisher)
    exec python -m affilio.worker.publisher
    ;;
  exporter)
    exec python -m affilio.worker.exporter
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    exec "$@"
    ;;
esac
