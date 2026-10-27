# Deploying AFFILIO on a Synology NAS

AFFILIO runs as five containers managed by Docker Compose on DSM (postgres, api, worker,
publisher, web). This guide is written for
DSM 7.2 with **Container Manager**. Nothing here modifies the existing video-processing
workflow: originals and proxies are mounted **read-only**, and AFFILIO writes only to its own
thumbnail, export, and database folders.

> **Do not deploy to the production NAS without explicit approval.** Walk through this guide on
> a test share first.

## 1. Prerequisites

| Item | Requirement |
| --- | --- |
| DSM | 7.2 or later with **Container Manager** installed (Package Center) |
| CPU | x86_64 (Intel/AMD "Plus" models) builds and runs the images directly. ARM64 models (Realtek RTD1619B, etc.) need images built with `docker buildx --platform linux/arm64` on another machine and loaded/pushed to a registry. 32-bit ARM models are not supported. |
| RAM | 4 GB minimum; 8 GB recommended. Default limits total ≈ 2.2 GB. |
| Disk | A few hundred MB for images, plus thumbnails (≈ 50–150 KB per video) and the database. |
| SSH | Enabled (Control Panel → Terminal & SNMP) for the initial setup and backups. |
| Network | LAN access to the NAS. **No port forwarding** from the internet until authentication exists. |

Check the architecture over SSH: `uname -m` → `x86_64` or `aarch64`.

## 2. Folder layout and permissions

Create the AFFILIO-owned folders. Example for volume 1:

```
/volume1/docker/affilio/
├── compose/           docker-compose.yml + .env   (this repo's infrastructure/docker/synology)
├── data/postgres/     PostgreSQL data directory
├── thumbnails/        generated thumbnails (read-write)
├── exports/           future publish-ready exports (read-write)
└── backups/           pg_dump files
```

Existing media stays where it is, for example:

```
/volume1/video/originals/   camera originals        → mounted read-only at /media/originals
/volume1/video/proxies/     HEVC proxies (existing)  → mounted read-only at /media/proxies
/volume1/video/finals/      finished edits (editors) → mounted read-only at /media/finals
```

The finals folder is where editors drop finished files. AFFILIO only reads it, indexes files
whose modification time is older than `MEDIA_FINAL_SETTLE_SECONDS` (default 120 s) and that do
not carry temp-name markers (`.part`, `.tmp`, …), and puts each final into a human review gate
(`pending_review` → `approved`/`rejected`) before anything may be published.

### Which user runs the containers

The containers run as `AFFILIO_UID:AFFILIO_GID` (defaults `1026:100`, the first DSM admin
account and the `users` group). That identity must be able to:

- **read** every file under the originals and proxies shares (DSM: Shared Folder → Permissions
  → Read Only for the group, *or* use an existing account that already has read access), and
- **read/write** `thumbnails/`, `exports/`, and `data/`.

Set ownership over SSH once:

```bash
sudo mkdir -p /volume1/docker/affilio/{compose,data/postgres,thumbnails,exports,backups}
sudo chown -R 1026:100 /volume1/docker/affilio
sudo chmod -R u+rwX,g+rwX,o-rwx /volume1/docker/affilio
```

Find the uid/gid of an account with `id <username>`. If you change them, change
`AFFILIO_UID`/`AFFILIO_GID` in `.env` and rebuild (`--build`), because the images bake the user.

Synology's `@eaDir`, `#recycle`, and `#snapshot` directories are skipped by the scanner.

## 3. Images

Two images, built from this repository:

| Image | Dockerfile | Contents |
| --- | --- | --- |
| `affilio-api` | `apps/api/Dockerfile` | Python 3.12, FastAPI, Alembic, **ffmpeg/ffprobe**, worker. Non-root. |
| `affilio-web` | `apps/web/Dockerfile` | Next.js standalone server. Non-root. |

**Option A — build on the NAS** (x86_64, slower but no registry):

```bash
ssh admin@nas
git clone <repo> /volume1/docker/affilio/src && cd /volume1/docker/affilio/src
cp infrastructure/docker/synology/.env.example infrastructure/docker/synology/.env
# edit .env (paths, secrets)
docker compose -f infrastructure/docker/synology/docker-compose.yml \
  --env-file infrastructure/docker/synology/.env build
```

**Option B — build elsewhere and push** (recommended, required for ARM64):

```bash
docker buildx build --platform linux/amd64 -f apps/api/Dockerfile -t registry.local/affilio-api:1.0.0 --push apps/api
docker buildx build --platform linux/amd64 -f apps/web/Dockerfile  -t registry.local/affilio-web:1.0.0 --push .
# then set AFFILIO_API_IMAGE / AFFILIO_WEB_IMAGE in .env
```

Keep `NEXT_PUBLIC_API_URL` empty for the web image (the default): the browser talks to the web
container only, which proxies `/api/*` to the API over the internal network.

## 4. Configuration (`.env`)

Copy `infrastructure/docker/synology/.env.example` to `.env` **next to the compose file** and set:

| Variable | Meaning |
| --- | --- |
| `AFFILIO_ORIGINALS_HOST_PATH` | Host folder of original videos. Mounted **read-only**. |
| `AFFILIO_PROXIES_HOST_PATH` | Host folder of existing proxies. Mounted **read-only**. |
| `AFFILIO_FINALS_HOST_PATH` | Host folder of finished videos from editors. Mounted **read-only**. |
| `MEDIA_FINAL_SETTLE_SECONDS` | Finals modified more recently than this are skipped until the upload settles. |
| `AFFILIO_THUMBNAILS_HOST_PATH` | AFFILIO-owned folder for thumbnails. Read-write. |
| `AFFILIO_EXPORTS_HOST_PATH` | AFFILIO-owned folder for future exports. Read-write. |
| `AFFILIO_DATA_HOST_PATH` | Parent of the PostgreSQL data directory. |
| `AFFILIO_UID` / `AFFILIO_GID` | Container user (see §2). |
| `AFFILIO_BIND_ADDRESS` / `AFFILIO_WEB_PORT` | Where the web UI listens. Use the NAS LAN IP. |
| `POSTGRES_PASSWORD` | Generate: `openssl rand -hex 32`. |
| `MEDIA_ADMIN_TOKEN` | Generate: `openssl rand -hex 32`. Service token for CLI/cron calls (scan triggers, release-due). Since M3A the web container does **not** hold it; browsers authenticate with session cookies. |
| `SESSION_COOKIE_SECURE` | `false` when the UI is served over plain HTTP on the LAN (no TLS); otherwise leave unset so the cookie is `Secure`. |
| `SESSION_TTL_DAYS` | Session lifetime (sliding). Default 14. |
| `META_APP_ID` / `META_APP_SECRET` / `META_OAUTH_REDIRECT_URI` | Meta app settings (M3B.1A). Leave empty until the app exists: the accounts page then shows which names are missing and "Connect Facebook Page" is disabled. Production always runs the live provider. See `docs/integrations/meta-app-activation-checklist.md`. |
| `CREDENTIAL_ENCRYPTION_KEYS` / `CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID` | Key ring for the credential vault (`openssl rand -base64 32`). **Back it up outside the database backups**; without it every connected Page must be reconnected. |
| `MEDIA_PROXY_PATTERNS` | How a proxy filename derives from an original (`{stem}`, `{name}`, `{ext}` placeholders), same relative directory under the proxies root. **Confirm against the real proxy naming scheme before the first scan.** |
| `MEDIA_VIDEO_EXTENSIONS` | Extensions to index. |
| `*_CPUS` / `*_MEMORY` | Resource limits (§6). |

Application paths inside the containers are fixed (`/media/originals`, `/media/proxies`,
`/data/thumbnails`, `/data/exports`); only host paths change. The database stores logical
roots and relative paths, never host paths, so moving a share only requires a `.env` change.

## 5. First start

```bash
cd /volume1/docker/affilio/src/infrastructure/docker/synology
docker compose --env-file .env up -d
docker compose --env-file .env ps          # all four "healthy"
docker compose --env-file .env logs -f api # "applying database migrations" then uvicorn
```

Seed the demo organization once (there is no sign-up flow yet):

```bash
docker compose --env-file .env exec -e APP_ENV=development api python -m affilio.scripts.seed_dev
```

Open `http://<NAS-LAN-IP>:3080/content`, check that the four root chips show `ok`
(originals/proxies readable, thumbnails/exports writable), and press **Scan now**. The worker
container runs the scan and then renders thumbnails. Progress is visible in the scan card and
`docker compose logs -f worker`.

You can also scan from the shell:

```bash
docker compose --env-file .env exec worker python -m affilio.scripts.media_scan --thumbnails
```

In Container Manager, the stack appears under **Project** once you import the compose file
through the UI instead of the CLI; both methods are equivalent.

## 6. Resource limits

Defaults (set in `.env`) suit a 4-core / 8 GB NAS:

| Service | CPUs | Memory | Notes |
| --- | --- | --- | --- |
| postgres | 1.0 | 512M | `shm_size` 128M |
| api | 1.0 | 512M | one uvicorn worker |
| worker | 2.0 | 768M | ffprobe/ffmpeg run single-threaded; `MEDIA_THUMBNAIL_CONCURRENCY` controls parallel renders (default 1) |
| publisher | 0.5 | 256M | Reels publisher (M3B.2A): streams finals in 4 MiB chunks, no transcoding; `PUBLISHER_MAX_CONCURRENT_UPLOADS` (default 1) |
| web | 1.0 | 384M | |

Lower the worker's CPU share if the NAS also transcodes for the existing pipeline. Scans are
I/O-bound and read at most 3 MiB per changed file; full hashes are only computed to confirm
duplicates.

## 7. Health checks

| Check | Command / URL |
| --- | --- |
| Containers | `docker compose ps` — every service `healthy` |
| Web | `curl -fsS http://<ip>:3080/` |
| API through the web proxy | `curl -fsS http://<ip>:3080/api/v1/health` and `/api/v1/system/info` (`database.status: ok`) |
| Worker | `docker compose exec worker python -m affilio.worker --healthcheck; echo $?` → `0` (heartbeat < 2 min old) |
| Publisher | `docker compose exec publisher python -m affilio.worker.publisher --healthcheck; echo $?` → `0`; the Distribution → Queue page shows the worker |
| Scan state | `curl -fsS http://<ip>:3080/api/v1/media/scans/status` |
| Logs | `docker compose logs --since 1h api worker` (JSON lines, `request_id` on every API line) |

The API and PostgreSQL publish no ports; probe them through the web proxy or `docker compose exec`.

## 8. Backups

The database is the only state besides thumbnails (which can be regenerated) and the
credential vault key in `.env`: dumps contain only ciphertext, so keep a copy of
`CREDENTIAL_ENCRYPTION_KEYS` in a password manager. A restore without the key leaves Pages as
"reconnect needed" (tokens are re-issued by Meta on reconnect); nothing else is lost.

```bash
# Logical dump (compressed, custom format), keeps the last 14 files
POSTGRES_PASSWORD=... ./scripts/db-backup.sh /volume1/docker/affilio/backups affilio-postgres
```

Schedule it with DSM **Task Scheduler → User-defined script** (daily, as root or the AFFILIO
user), and include `/volume1/docker/affilio/backups` plus `thumbnails/` in Hyper Backup.
Stopping the stack and copying `data/postgres` is also a valid cold backup.

### Restore

```bash
docker compose --env-file .env stop api worker web
./scripts/db-restore.sh /volume1/docker/affilio/backups/affilio-YYYYMMDD-HHMMSS.dump affilio-postgres
docker compose --env-file .env start api worker web
```

`db-restore.sh` asks for confirmation; it drops and recreates objects inside the existing database.

## 9. Upgrade and rollback

Upgrade:

1. `./scripts/db-backup.sh` (always).
2. Pull or build the new images (`docker compose pull` or `docker compose build`).
3. `docker compose up -d` — the API container applies migrations on start
   (`RUN_MIGRATIONS_ON_START=true`), then the worker and web restart on the new version.
4. Verify §7 health checks and open the Content Library.

Rollback:

1. `docker compose down` (data stays in the bind mounts).
2. Set `AFFILIO_API_IMAGE` / `AFFILIO_WEB_IMAGE` back to the previous tag.
3. If the upgrade applied a migration that the previous version cannot run against, restore the
   pre-upgrade dump (§8) **or** downgrade the schema explicitly:
   `docker compose run --rm api alembic downgrade <previous revision>`.
4. `docker compose up -d` and re-verify.

Every migration in this repository has a tested `downgrade()`; the release notes list the
revision for each version.

## 10. Security posture (M3A)

- **Authentication is required** for every page and API call (session cookies, argon2id
  passwords, roles viewer/editor/admin/owner; see `docs/architecture/security.md`). Create the
  first owner with `docker compose exec api python -m affilio.scripts.create_user --email … --org demo-org --role admin`
  after creating the organization with the seed (development) or a migration-time insert;
  the development seed must not be used in production (`APP_ENV=production` refuses it).
- Keep the port LAN-only regardless (DSM Firewall: allow the LAN subnet, deny everything else).
  Login rate limiting exists (pre-M3B hardening) but the UI still must not be exposed
  through QuickConnect or a port forward without TLS in front. If you terminate TLS on the DSM reverse proxy, leave `SESSION_COOKIE_SECURE` unset;
  on plain HTTP set it to `false` or logins will not stick.
- The service token (`MEDIA_ADMIN_TOKEN`) is only for CLI/cron calls against the API
  container; the web container no longer receives it.
- No platform credentials exist: manually registered Pages are labelled unverified, and
  publishing is assisted (operator publishes, records the URL). API publishing arrives with
  Meta authorization in M3B.
- Interactive API docs are disabled (`DOCS_ENABLED=false`).
- PostgreSQL has no published port; the password lives only in `.env` (mode 600).
- Originals and proxies are read-only bind mounts; even a compromised container cannot alter them.

## 11. Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| Root chip "originals unavailable" | Host path wrong in `.env`, or the container user cannot read it (`ls -ln` on the share; check Shared Folder permissions). |
| Scan fails with `permission_denied` entries | Specific subfolders unreadable by `AFFILIO_UID`. Fix ACLs or exclude them. |
| Thumbnails stay pending | Worker not running or unhealthy (`docker compose logs worker`); thumbnails folder not writable. |
| Thumbnail failed: `ffmpeg timed out` | Very large originals over a slow share. Make sure proxies are linked (pattern), or raise `MEDIA_THUMBNAIL_TIMEOUT_SECONDS`. |
| No proxies linked | `MEDIA_PROXY_PATTERNS` does not match the proxy naming; inspect an original/proxy pair and adjust. |
| `scan_already_active` | A previous scan is queued/running; wait or check `/api/v1/media/scans`. |
| Web shows "API unreachable" | API container unhealthy; `docker compose logs api`. |
