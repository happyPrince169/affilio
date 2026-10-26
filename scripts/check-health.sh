#!/usr/bin/env bash
# Probes the running services and prints a compact status table.
set -uo pipefail

API="${NEXT_PUBLIC_API_URL:-http://localhost:8000}"
WEB="${WEB_URL:-http://localhost:3000}"

check() {
  local label="$1" url="$2"
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" || echo "000")
  if [ "$code" = "200" ]; then
    printf '  \033[32m✔\033[0m %-28s %s\n' "$label" "$url"
  else
    printf '  \033[31m✘\033[0m %-28s %s (HTTP %s)\n' "$label" "$url" "$code"
    return 1
  fi
}

echo "AFFILIO health check"
status=0
check "API liveness"      "$API/health"               || status=1
check "API v1 health"     "$API/api/v1/health"        || status=1
check "API system info"   "$API/api/v1/system/info"   || status=1
check "Web dashboard"     "$WEB/"                     || status=1

if command -v docker >/dev/null 2>&1; then
  db=$(docker inspect -f '{{.State.Health.Status}}' affilio-postgres 2>/dev/null || echo "not running")
  printf '  %s postgres container            %s\n' "$([ "$db" = healthy ] && echo $'\033[32m✔\033[0m' || echo $'\033[31m✘\033[0m')" "$db"
  [ "$db" = healthy ] || status=1
fi

exit $status
