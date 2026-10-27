# NAS workflow: sync and deploy are separate commands

Two explicit, independent operations, both run from the laptop against the Synology NAS
over Tailscale SSH (`leosunnas`), both scoped to the AFFILIO project directory
`/volume1/docker/affilio/`:

| Command | What it does | What it never does |
| --- | --- | --- |
| `pnpm nas:sync` | rsync the local source tree to `/volume1/docker/affilio/` and record the synced git revision | build images, restart containers, run migrations, deploy |
| `pnpm nas:deploy` | build and (re)start **only** the `affilio` compose project from the source already on the NAS, with backup, migrations and health checks | rsync, copy files from the laptop, `git pull`, touch other NAS projects |
| `pnpm nas:status` | read-only view: synced revision, last deployment, `.env` presence, containers, images, disk, health URLs | change anything |
| `pnpm nas:dry-run` | show what `nas:sync` would change | transfer anything |

Daily workflow: code locally → `pnpm nas:sync` when the NAS copy should be updated →
`pnpm nas:deploy` when that copy should run. A deploy can be repeated any number of times
without another sync, and a sync never causes a deploy. There is no watch mode and no
fswatch dependency any more.

```
laptop ──(1) pnpm nas:sync: rsync over ssh──► /volume1/docker/affilio/  (source + .deploy/source-revision)
                                                      │
                 (2) pnpm nas:deploy: ssh -t, runs scripts/nas-deploy-remote.sh ON the NAS
                                                      ▼
                       docker compose -p affilio build → migrate → up -d → health checks
```

## Sync (`scripts/nas-sync.sh`)

- rsync over SSH with `~/.ssh/id_ed25519`, `BatchMode`, keep-alives; one sync at a time
  (lock); exit codes 0 ok · 1 config · 2 unreachable · 3 destination missing · 4 busy ·
  5 rsync failed.
- Destination guard: absolute, under `/volume1/docker/`, never the prefix itself, never
  another service (`content-factory`, `meobot`, `canonora`, `reconnect_tailscale`), no shell
  metacharacters. The script never creates the directory.
- **Deleted source files are deleted on the NAS too** (`RSYNC_DELETE=1` default), so a file
  removed locally cannot linger and end up in a build. Everything that is NAS-only is
  protected on the receiver and never deleted or overwritten, with or without delete:
  `data/`, `backups/`, `thumbnails/`, `exports/`, `uploads/`, `logs/`, `media/`, `.deploy/`,
  every `.env` / `.env.*` (except the committed `.env.example` files, which are source).
  `RSYNC_DELETE=0` keeps remote files.
- `.rsyncignore` keeps generated output, caches, virtualenvs, secrets, logs, databases,
  Docker volumes, backups, uploads and media off the wire; `apps/web/public/**` is always
  shipped. Dockerfiles, compose, migrations, lockfiles, scripts and docs always travel.
- After a successful sync the file `.deploy/source-revision` on the NAS records the local
  commit (`revision=…`, `working_tree=clean|dirty`, time, hostname); `nas:status` and the
  deploy preflight show it, so you always know which code a deployment used.

## Deploy (`scripts/nas-deploy.sh` → `scripts/nas-deploy-remote.sh` on the NAS)

`pnpm nas:deploy` opens an interactive SSH session (the NAS Docker socket is root-only, so
`sudo docker` asks for your NAS password once) and runs the copy of
`scripts/nas-deploy-remote.sh` that the last sync placed on the NAS. Sub-commands:

| | |
| --- | --- |
| `pnpm nas:deploy check` | preflight only, changes nothing; needs no sudo (the compose file is only parsed), so `NAS_SSH_TTY=0 pnpm nas:deploy check` works non-interactively |
| `pnpm nas:deploy deploy` | full deployment (default when you type `pnpm nas:deploy deploy`) |
| `pnpm nas:deploy rollback` | switch back to the previous images |

Deployment steps, each logged, each aborting the run on failure:

1. **Preflight**: compose file and `.env` present (`.env` lives at
   `/volume1/docker/affilio/infrastructure/docker/synology/.env`, created by you on the NAS,
   never synced), required variables set and not the example values, host folders exist,
   `docker compose config` valid, `META_LIVE_PUBLISHING_ENABLED` not true,
   `META_PROVIDER_MODE` live. The synced revision and the last deployment are printed.
2. **Confirmation**: the first deployment on a NAS requires typing the project directory;
   later ones ask `y/N` (`AFFILIO_DEPLOY_YES=1` skips that for repeat deployments only).
3. **Backup**: if `affilio-postgres` is running, `pg_dump` to
   `/volume1/docker/affilio/backups/pre-deploy-<timestamp>.dump`.
4. **Rollback target**: current `affilio-api:latest` / `affilio-web:latest` are re-tagged
   `:previous`.
5. **Build**: `docker compose -p affilio build --pull api web` (only AFFILIO images).
6. **Migrate**: `postgres` is started and waited for, then
   `compose run --rm --no-deps api migrate` (`alembic upgrade head`).
7. **Start**: `compose up -d --remove-orphans` (only the `affilio` project), then every
   service must report healthy within `HEALTH_TIMEOUT` (240 s).
8. **HTTP checks**: `/api/v1/health` and `/api/v1/system/info` through the web port
   (`database.status = ok`, `meta.mode = live`).
9. **Record**: `.deploy/last-deploy` with time, revision and image ids.

Other NAS projects (`content-factory`, `meobot`, `canonora`) are never referenced; the
script uses `--project-name affilio` and the AFFILIO compose file only, and never runs
`down -v` or any `prune`.

### Failure handling and rollback

| Failed step | Effect | What to do |
| --- | --- | --- |
| preflight / compose-config | nothing changed | fix `.env` or the compose file, run `pnpm nas:deploy check` |
| build | nothing changed (old containers keep running) | read the build log, fix the source, `pnpm nas:sync`, deploy again |
| migrate | schema may be partially upgraded; app containers not yet restarted | inspect `docker compose logs api`; fix and re-run, or restore `backups/pre-deploy-*.dump` with `scripts/db-restore.sh` |
| up / health | new containers may be unhealthy | `pnpm nas:status`; `pnpm nas:deploy rollback` restores the previous images; if the new migration is incompatible with the old code, also restore the pre-deploy dump or `alembic downgrade <revision>` |

`pnpm nas:deploy rollback` re-tags `:previous` → `:latest`, restarts the AFFILIO services
without building, waits for health, and prints the database instructions. Every migration
in this repository has a tested `downgrade()`.

To run the preflight with the laptop's copy of the script (for example after fixing it, before
syncing) stream it over SSH: `ssh leosunnas 'AFFILIO_PROJECT_DIR=/volume1/docker/affilio bash -s check' < scripts/nas-deploy-remote.sh`.

## Status (`scripts/nas-status.sh` → `scripts/nas-status-remote.sh` on the NAS)

Prints the synced revision, last deployment, `.env` and runtime folder presence, disk, the
`affilio` containers with health, image tags and the health endpoints. Read-only. Like the
deploy, it runs the synced copy of its remote script so the SSH session keeps a terminal and
`sudo` can prompt for the container section; without a password (or `NAS_SSH_TTY=0`) the
rest is still shown.

## Cloudflare Tunnel connector (`pnpm nas:tunnel`)

The public hostname `app.aaffilio.online` reaches the NAS through a **remotely managed**
Cloudflare Tunnel (`affilio-nas`, id `28cdb9b3-be9f-4d71-bb9e-7fff3442b02d`): the route
`app.aaffilio.online → http://192.168.1.3:3080` is configured in the Cloudflare dashboard, and
the NAS only runs a connector container defined in
`infrastructure/docker/synology/cloudflared/docker-compose.yml` (compose project
`affilio-tunnel`, separate from `affilio` so deployments never touch it; no ports, no volumes,
read-only filesystem, 128 M memory limit, `restart: unless-stopped`).

The connector token is the only secret. It lives in `cloudflared/.env` **on the NAS** (mode
600), is git-ignored, excluded from and protected against `nas:sync`, and is passed to the
container as an environment variable (never as a command-line argument).

| Command | What it does |
| --- | --- |
| `pnpm nas:tunnel config` | validates the compose file and `.env` on the NAS (no sudo) |
| `pnpm nas:tunnel up` | starts or updates the connector (sudo prompt) |
| `pnpm nas:tunnel status` | container state and the last 20 log lines |
| `pnpm nas:tunnel logs` | follow the log |
| `pnpm nas:tunnel restart` / `down` | restart / stop and remove the connector container |

Supplying the token without echoing it (run from the laptop; the token is typed into the
NAS session and written straight to the file):

```bash
pnpm nas:sync                                   # the cloudflared compose file must be on the NAS
ssh -t leosunnas 'umask 077; d=/volume1/docker/affilio/infrastructure/docker/synology/cloudflared;   read -r -s -p "TUNNEL_TOKEN: " t; echo; printf "TUNNEL_TOKEN=%s\nCLOUDFLARED_MEMORY=128M\n" "$t" > "$d/.env";   chmod 600 "$d/.env"; ls -l "$d/.env"'
pnpm nas:tunnel config
pnpm nas:tunnel up
pnpm nas:tunnel status                           # expect "Registered tunnel connection" lines
```

Then in Cloudflare Zero Trust: the tunnel shows **Healthy**, and `https://app.aaffilio.online`
serves the login page. Protect the hostname with a Cloudflare Access policy before sharing it.

## Persistent data vs replaceable source

| Lives in `/volume1/docker/affilio/` | Owner | Touched by sync | Touched by deploy |
| --- | --- | --- | --- |
| `apps/`, `packages/`, `infrastructure/`, `scripts/`, `docs/`, lockfiles | laptop (git) | replaced, stale files removed | read |
| `infrastructure/docker/synology/.env` | NAS operator | never | read |
| `data/postgres/`, `backups/`, `thumbnails/`, `exports/` | NAS runtime | never | written by containers / backup step |
| `.deploy/` | sync + deploy records | `source-revision` written | `last-deploy` written |
| media shares (originals, proxies, finals) | ContentFactory | never (outside the project dir, read-only mounts) | read-only mounts |

## Setup once

1. `ssh -o BatchMode=yes leosunnas 'echo ok'` works (Tailscale up, key in `~/.ssh/config`).
2. Project directory exists: `ssh leosunnas 'mkdir -p /volume1/docker/affilio'` (yours to run).
3. First sync: `pnpm nas:sync`.
4. On the NAS: create `/volume1/docker/affilio/infrastructure/docker/synology/.env` from the
   `.env.example` next to it (`chmod 600`), the runtime folders
   (`sudo mkdir -p /volume1/docker/affilio/{data/postgres,thumbnails,exports,backups} && sudo chown -R 1026:100 /volume1/docker/affilio`),
   and the DSM firewall rule for the web port (`synology-runbook.md` §1–§3).
5. `pnpm nas:deploy check` until it reports "preflight OK", then `pnpm nas:deploy deploy`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| sync exit 2 | Tailscale down or NAS asleep: `tailscale status`, `ssh leosunnas` |
| sync exit 3 | create the project directory (command above) or fix ownership (`chown 1026:100`) |
| sync exit 4 | another sync is running |
| deploy: "missing .env" | create it on the NAS; it is never synced |
| deploy: "META_LIVE_PUBLISHING_ENABLED is enabled" | set it to `false`; live publishing needs the activation checklist first |
| deploy: "cannot talk to Docker" | wrong sudo password, or run `pnpm nas:deploy` from a real terminal (needs a tty) |
| deploy: "nas-deploy-remote.sh is missing on the NAS" | run `pnpm nas:sync` once with the current repository |
| status shows an older revision than expected | you have not synced since that commit; sync and deploy are independent by design |
