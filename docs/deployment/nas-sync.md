# Syncing AFFILIO source to the NAS (Tailscale + SSH + rsync)

A one-command workflow that mirrors the local AFFILIO checkout to the project folder on the
Synology NAS (`/volume1/docker/affilio/`, the same folder the deployment runs from) while you
keep coding. It copies **source only**: it never builds images, never
starts or restarts containers, never touches the ContentFactory media library or any other
service on the NAS. Deploying is a separate, deliberate step (`synology-runbook.md`).

```
laptop (fswatch) ──rsync over ssh (Tailscale)──► leosunnas:/volume1/docker/affilio/
```

## Daily use

| Command | What it does |
| --- | --- |
| `pnpm nas:watch` | sync now, then on every local change until Ctrl-C |
| `pnpm nas:sync` | one incremental sync |
| `pnpm nas:dry-run` | list what would be transferred; nothing is written |

Stop watch mode with **Ctrl-C** (the current rsync finishes, then the loop exits). Only one
sync runs at a time on a machine; a second `watch`/`sync` exits with code 4.

## Initial setup

1. **Tailscale** up on both machines and the SSH alias `leosunnas` working without a prompt:
   ```bash
   ssh -o BatchMode=yes leosunnas 'echo ok'
   ```
   The alias lives in `~/.ssh/config` (user, hostname, `IdentityFile ~/.ssh/id_ed25519`).
   The script only passes `-i $SSH_KEY`, `BatchMode=yes` and keep-alives; it changes no SSH
   or Tailscale configuration.
2. **fswatch** on the laptop (watch mode only): `sudo apt install fswatch`. rsync and ssh
   are standard.
3. **Project folder on the NAS** (one time, create it yourself after review):
   ```bash
   ssh leosunnas 'mkdir -p /volume1/docker/affilio'
   ```
   The script never creates NAS directories; `once`/`watch` stop with exit code 3 and print
   this command when the folder is missing. `dry-run` still works.

## Configuration

Environment variables, all optional:

| Variable | Default | Meaning |
| --- | --- | --- |
| `NAS_HOST` | `leosunnas` | SSH host alias |
| `NAS_DEST` | `/volume1/docker/affilio/` | destination; must be an absolute subdirectory under `NAS_DEST_PREFIX`, may not name another service (`content-factory`, `meobot`, `canonora`, …). The NAS runtime folders inside it (`data/`, `backups/`, `thumbnails/`, `exports/`) and its `.env` files are protected on the receiver even with `RSYNC_DELETE=1` |
| `NAS_DEST_PREFIX` | `/volume1/docker/` | allowed destination root |
| `SSH_KEY` | `~/.ssh/id_ed25519` | private key |
| `RSYNC_DELETE` | `0` | `1` also removes remote files deleted locally (opt-in; never the default) |
| `DRY_RUN` | `0` | `1` makes `once`/`watch` run rsync with `--dry-run` |
| `DEBOUNCE_SECONDS` | `1.5` | fswatch batching window |
| `NAS_SYNC_LOG` | unset | append log lines to a file as well |

## What is and is not transferred

`.rsyncignore` keeps generated output, caches, virtual environments, `.env` files (only the
`.env.example` files travel), keys and certificates, logs, temporary files, databases and
dumps, Docker volumes, backups, uploads/thumbnails/exports and media files off the wire.
Everything needed to build and run on the NAS goes across: `apps/*` source, Dockerfiles,
`infrastructure/docker/synology/docker-compose.yml`, Alembic migrations, `pnpm-lock.yaml`,
`apps/api/uv.lock`, `scripts/`, `docs/`.

Verify at any time:

```bash
pnpm nas:dry-run 2>&1 | grep -E '\.env$|\.venv/|node_modules/|\.next/' || echo "no secrets or build output listed"
```

## Source sync vs deployment

| | Source sync (`pnpm nas:watch`) | Deployment (`synology-runbook.md`) |
| --- | --- | --- |
| What moves | files in the repo | nothing; builds images from the synced tree |
| When | continuously while coding | when you decide |
| Effect on containers | none | `docker compose build` / `up -d` restarts services |
| Safety | project folder only, no `--delete`, runtime folders protected | explicit approval, backups first |

After syncing, a deployment is still a manual sequence on the NAS, for example:

```bash
cd /volume1/docker/affilio/infrastructure/docker/synology
docker compose --env-file .env build && docker compose --env-file .env up -d
```

Nothing in the sync script runs these.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| exit 2 "unreachable over SSH" | Tailscale down or NAS asleep: `tailscale status`, `ssh leosunnas` |
| exit 3 "missing or not writable" | create the project folder (command above) or fix its ownership (`chown 1026:100`) |
| exit 4 "another nas-sync is running" | a watch is already open in another terminal |
| exit 5 "rsync FAILED" (once) | read the rsync error; in watch mode the loop retries with backoff (5 s → 120 s) |
| exit 6 "fswatch is not installed" | `sudo apt install fswatch` |
| exit 1 "NAS_DEST …" | the destination failed validation: absolute, under the prefix, not another service |
| Permission denied on the NAS | files are written as the SSH user (uid 1026); keep that user as the owner of the staging folder |
| Static assets missing on the NAS | `apps/web/public/**` is force-included; check nothing else matches a pattern in `.rsyncignore` |
| Sync keeps firing | something writes inside the repo (build output, logs); add the path to `.rsyncignore` and the fswatch excludes in `scripts/nas-sync.sh` |
