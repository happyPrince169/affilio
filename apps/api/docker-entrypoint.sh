#!/bin/sh
# Entrypoint for the API image.
#   api     – (default) run migrations when RUN_MIGRATIONS_ON_START=true, then uvicorn
#   worker  – background scan/thumbnail worker
#   migrate – apply migrations and exit
#   <cmd>   – anything else is exec'd verbatim (e.g. `python -m affilio.scripts.media_scan`)
set -eu

case "${1:-api}" in
  api)
    if [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
      echo "[entrypoint] applying database migrations"
      alembic upgrade head
    fi
    exec uvicorn affilio.main:app \
      --host 0.0.0.0 --port 8000 \
      --workers "${UVICORN_WORKERS:-1}" \
      --no-access-log --proxy-headers --forwarded-allow-ips "*"
    ;;
  worker)
    exec python -m affilio.worker
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    exec "$@"
    ;;
esac
