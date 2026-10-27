# Synology deployment runbook (M3B.2A state)

Step-by-step procedure for the first deployment and for upgrades of the five-container
stack (`postgres`, `api`, `worker`, `publisher`, `web`) on a DS923+ (20 GB RAM). It
complements `synology.md` (reference) and records the readiness review of 2026-10-29.
**Do not run it on the production NAS without explicit approval.**

## 0. Readiness review (what was checked, what must change)

| Area | State in `infrastructure/docker/synology/` | Verdict |
| --- | --- | --- |
| Resource limits | memory only: postgres 512M, api 512M, worker 768M, publisher 256M, web 384M (≈ 2.4 GB total; `deploy.resources.limits.memory`). CPU limits removed after the first deployment attempt failed with "NanoCpus can not be set" (DSM kernel 4.4 has no CFS bandwidth control) | OK for 20 GB; raise `WORKER_MEMORY` to 1G if thumbnails of 4K originals time out |
| PostgreSQL persistence | bind mount `${AFFILIO_DATA_HOST_PATH}/postgres`, `--data-checksums`, no published port, `shm_size` 128M | OK |
| Read-only media mounts | originals, proxies, finals `read_only: true` on api and worker; publisher mounts **only** finals read-only; thumbnails/exports read-write | OK (matches CLAUDE.md §6b) |
| Encryption key | `CREDENTIAL_ENCRYPTION_KEYS` / `..._ACTIVE_KEY_ID` passed from `.env`; API answers 503 for connections without it | **Required before connecting any Page**; back the key up out of band |
| Session cookie | `SESSION_COOKIE_SECURE=false` in `.env.example` for plain-HTTP LAN | **Must be unset (Secure) once TLS terminates on the DSM reverse proxy**; the Meta redirect URI needs HTTPS anyway |
| Reverse proxy trust | API: `TRUST_PROXY_HEADERS=true`, uvicorn `--proxy-headers --forwarded-allow-ips "*"`; only the web container can reach the API (no published port) | Acceptable because the API is unreachable from outside the compose network; never publish the API port |
| Health checks | postgres `pg_isready`; api (image `HEALTHCHECK` on `/health`); worker and publisher heartbeat commands; web image `HEALTHCHECK` | OK; `depends_on … service_healthy` orders startup (api applies migrations first) |
| Restart policies | `restart: unless-stopped` on every service | OK |
| Log rotation | `json-file`, `max-size 10m`, `max-file 3` per container | OK (≈ 150 MB cap total) |
| Backups | `scripts/db-backup.sh` (custom-format dump, keeps 14), `db-restore.sh` (interactive, destructive) | OK; **plus** the vault key and `.env` out of band (dumps hold ciphertext only) |
| Mock isolation | compose forces `META_PROVIDER_MODE: live`, `APP_ENV: production`; settings refuse mock outside development/test; live Reels publishing off (`META_LIVE_PUBLISHING_ENABLED=false`) | OK: no mock Page can exist in production |
| Overdue schedule policy | `PUBLISHER_OVERDUE_GRACE_MINUTES=120`, `PUBLISHER_OVERDUE_POLICY=review` | OK; decisions audited |
| Internet exposure | web port bound to the LAN IP; no QuickConnect; DSM firewall rule required | Keep LAN-only until TLS is in place |

Security findings from the review are listed in §7.

## 1. Prepare the NAS (once)

1. DSM 7.2, Container Manager installed, SSH enabled for setup.
2. Create the folders and ownership (uid 1026 / gid 100 by default):
   ```bash
   sudo mkdir -p /volume1/docker/affilio/{data/postgres,thumbnails,exports,backups}
   sudo chown -R 1026:100 /volume1/docker/affilio
   sudo chmod -R u+rwX,g+rwX,o-rwx /volume1/docker/affilio
   ```
3. Confirm the media shares exist and are readable by that uid: originals, proxies, finals.
4. DSM Firewall: allow the LAN subnet to `AFFILIO_WEB_PORT`; deny everything else.
5. (For Meta connections) DSM reverse proxy with a certificate on the hostname that will be
   the OAuth redirect URI; then leave `SESSION_COOKIE_SECURE` unset.

## 2. Build or pull images

Build elsewhere (recommended) and push to a registry the NAS can pull, or build on the NAS:

```bash
docker buildx build --platform linux/amd64 -f apps/api/Dockerfile -t <registry>/affilio-api:<tag> --push apps/api
docker buildx build --platform linux/amd64 -f apps/web/Dockerfile  -t <registry>/affilio-web:<tag> --push .
```

The images verified in this review are `affilio-api:m3b2a` and `affilio-web:m3b2a` (built
from commit `HEAD` of 2026-10-29). Record the tag in `.env` (`AFFILIO_API_IMAGE`,
`AFFILIO_WEB_IMAGE`).

## 3. Configure `.env`

The project directory on the NAS is `/volume1/docker/affilio/` (filled by `pnpm nas:sync`,
see `nas-sync.md`); the compose directory is
`/volume1/docker/affilio/infrastructure/docker/synology/`. Copy its `.env.example` to `.env`
there (the sync never touches that file) and set, at minimum:

| Variable | Value |
| --- | --- |
| `AFFILIO_*_HOST_PATH` | the shares and AFFILIO folders from §1 |
| `AFFILIO_UID` / `AFFILIO_GID` | the account that can read the shares |
| `AFFILIO_BIND_ADDRESS` / `AFFILIO_WEB_PORT` | NAS LAN IP, e.g. `3080` |
| `POSTGRES_PASSWORD`, `MEDIA_ADMIN_TOKEN` | `openssl rand -hex 32` each |
| `CREDENTIAL_ENCRYPTION_KEYS=v1:<openssl rand -base64 32>`, `CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID=v1` | store the key in the password manager **first** |
| `SESSION_COOKIE_SECURE` | `false` only while the UI is plain HTTP on the LAN |
| `META_*` | empty until the Meta app exists (`docs/integrations/meta-app-activation-checklist.md`) |
| `META_LIVE_PUBLISHING_ENABLED` | `false` (do not change until `meta-live-publishing-activation.md`) |
| `PUBLISHER_MAX_CONCURRENT_UPLOADS` | `1` |
| `PUBLISHER_OVERDUE_GRACE_MINUTES` / `PUBLISHER_OVERDUE_POLICY` | `120` / `review` |

`chmod 600 .env`.

## 4. First start

From the laptop, with the source already synced (`pnpm nas:sync`):

```bash
pnpm nas:deploy check     # preflight: .env, folders, compose config, live publishing off
pnpm nas:deploy deploy    # confirmation → backup (if any) → build → migrate → up → health
pnpm nas:status           # read-only verification
```

`nas-sync.md` describes every step and the rollback path. The equivalent manual commands on
the NAS are:

```bash
cd /volume1/docker/affilio/infrastructure/docker/synology
sudo docker compose --env-file .env up -d
sudo docker compose --env-file .env ps                 # five services healthy
sudo docker compose --env-file .env exec api python -m affilio.scripts.create_user --email <owner> --org demo-org --role admin
```

The first organization must exist before `create_user`; on a fresh database run the seed
**only** through a development stack, or insert the organization with the documented SQL in
`synology.md` §10. Never run `seed_dev` against production (it refuses).

Checks:

- `curl -fsS http://<ip>:3080/api/v1/system/info` → `database.status: ok`,
  `integrations.meta.mode: live`, `configured: false` until the Meta app is set up.
- Sign in, open Content → root chips `ok`, **Scan now** (finals).
- Distribution → Queue → the engine card shows **1 worker**, **live publishing off**.
- Accounts → "Connect Facebook Page" is disabled with the list of missing settings.

## 5. Daily operation

- Backups: DSM Task Scheduler → user-defined script, daily:
  `POSTGRES_PASSWORD=… /volume1/docker/affilio/scripts/db-backup.sh /volume1/docker/affilio/backups affilio-postgres`;
  include `backups/`, `thumbnails/` and the `.env` (key) in Hyper Backup.
- Health: `docker compose ps`; `docker compose exec publisher python -m affilio.worker.publisher --healthcheck`.
- Missed schedules after an outage: jobs within the grace window publish on their own;
  older ones appear in the queue as **overdue · operator review** with a **Release now**
  button; every decision is in the audit log.
- Uncertain publications: **Needs reconciliation** → admin **Check with provider** (never
  re-sends); choose "not published" only after confirming on the Page.

## 6. Upgrade and rollback

1. `pnpm nas:sync` (the NAS copy now has the new source; nothing runs yet).
2. `pnpm nas:deploy deploy` — pre-deploy dump, previous images kept as `:previous`, build,
   migrations, restart of the five AFFILIO services, health checks.
3. `pnpm nas:status`.

Rollback: `pnpm nas:deploy rollback` (previous images, restart, health), plus the pre-deploy
dump restore or `alembic downgrade <revision>` when the new migration is incompatible.

Rollback: `docker compose down`, previous tags in `.env`, and either restore the pre-upgrade
dump or `docker compose run --rm api alembic downgrade <revision>` (every migration
0001–0010 has a tested downgrade), then `docker compose up -d`.

Restore drill (recommended once): stop api/worker/publisher/web, `db-restore.sh <dump>`,
start again; with the key present connected Pages come back as connected, without it they
show "reconnect needed" (tokens are re-issued on reconnect).

## 6a. Production environment record (prepared 2026-10-27, not yet deployed)

Prepared on `leosunnas` without starting anything. No secret values are recorded here.

| Item | State |
| --- | --- |
| Compose `.env` | `/volume1/docker/affilio/infrastructure/docker/synology/.env`, mode 600, owner uid 1026; `POSTGRES_PASSWORD`, `MEDIA_ADMIN_TOKEN` (64 hex chars) and `CREDENTIAL_ENCRYPTION_KEYS` (`v1:` + 32 random bytes, base64) generated on the NAS with `openssl rand`; back the key up out of band |
| Media roots (read-only bind mounts) | originals `/volume1/ContentFactory/fashion/originals`, proxies `/volume1/ContentFactory/fashion/proxies` (same relative names, `{stem}.mp4`), finals `/volume1/ContentFactory/fashion/FINAL`. `/volume1/ContentFactory/finals/` does not exist: ContentFactory is organised per channel (`fashion`, `Only channel`, …); other channels need either a second deployment root decision or multi-root support (not in scope) |
| Runtime folders | `/volume1/docker/affilio/{data/postgres,thumbnails,exports,backups}`, uid 1026 / gid 100, mode 770; protected from `nas:sync` deletion |
| Web port | `192.168.1.3:3080` (LAN only; the API and PostgreSQL publish no ports; another PostgreSQL already listens on the NAS loopback 5432, which does not conflict) |
| Origins / cookies | `CORS_ORIGINS=https://app.aaffilio.online`; `SESSION_COOKIE_SECURE` unset → `Secure` cookie (production); same-origin `/api` proxy, so the browser only ever talks to `app.aaffilio.online` |
| Meta | `META_PROVIDER_MODE=live` (fixed by compose), `META_LIVE_PUBLISHING_ENABLED=false`, app id/secret empty (connections refused with a clear "not configured" message), `META_OAUTH_REDIRECT_URI=https://app.aaffilio.online/api/v1/accounts/connect/facebook/callback` pre-set for the future app |
| Stray file | `/volume1/docker/affilio/.env` (byte-identical copy of the dev template, not read by compose) can be deleted; the compose directory `.env` is the real one |

### Cloudflare Tunnel targets (to configure after deployment)

| Public hostname | Tunnel service | Notes |
| --- | --- | --- |
| `app.aaffilio.online` | `http://192.168.1.3:3080` (the `affilio-web` container; or `http://affilio-web:3000` if `cloudflared` joins the `affilio` compose network) | the only hostname the browser needs: the Next.js route handler proxies `/api/*` to `http://api:8000` inside the compose network |
| `api.aaffilio.online` | not required; if created, point it at the same web service, **not** at the API container | `NEXT_PUBLIC_API_URL` is baked into the web image as `""` (same origin). Pointing the browser at `api.aaffilio.online` would make the session cookie cross-site (`SameSite=Lax`, host-scoped) and break login; the OAuth callback also relies on the `app` host cookie |

Cloudflare Access: protect `app.aaffilio.online` as a whole. Paths that must stay reachable by
the people who use them are all browser flows behind the same login, including the Meta
OAuth callback (`/api/v1/accounts/connect/facebook/callback`), so no bypass is needed for
them. Server-to-server callbacks from Meta (deauthorize / data deletion) are not configured
and would need an Access bypass rule if ever enabled; the CLI service token is used from the
NAS itself, not through the tunnel. Rate limiting behind the tunnel keys on the first
`X-Forwarded-For` hop that `cloudflared` sets (`TRUST_PROXY_HEADERS=true` is correct because
the API is only reachable through the web proxy); LAN clients hitting `:3080` directly could
spoof that header, which is acceptable on the private LAN.

## 7. Security findings (2026-10-29 review)

| # | Finding | Severity | Action |
| --- | --- | --- | --- |
| 1 | `SESSION_COOKIE_SECURE=false` example for plain-HTTP LAN | medium | unset once TLS terminates on the DSM proxy (required for Meta OAuth anyway) |
| 2 | uvicorn trusts `X-Forwarded-*` from any peer (`--forwarded-allow-ips "*"`) | low | acceptable only because the API has no published port; never add `ports:` to `api` |
| 3 | Vault key lives in `.env` on the NAS volume | medium | `chmod 600`, out-of-band backup, rotation runbook (`security.md`) |
| 4 | Mock fault switches are process-local; a stray `META_PROVIDER_MODE=mock` would be refused in production | info | enforced by settings validation; keep `APP_ENV=production` in compose |
| 5 | Service token acts as owner on its allow-listed routes | low | narrowed in M3B.1A; rotate `MEDIA_ADMIN_TOKEN` if it was ever shared |
| 6 | Login rate limiting is in-process (one uvicorn worker) | info | keep `UVICORN_WORKERS=1` |
| 7 | Live Reels calls untested against Meta | blocker for live publishing | follow `meta-live-publishing-activation.md` on a test Page first |
