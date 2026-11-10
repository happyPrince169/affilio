# P4B Gate 1 — deploy AFFILIO `413e2ab` to the NAS (operator runbook)

Prepared 2026-11-10 from Gate 0 facts (6 healthy containers, revision `0018_upload_root`,
0 products, network `affilio_default` = `172.19.0.0/16`, no ingest network yet). Nothing in
this document has been executed. Every step is run by the operator; each gate says what
"pass" means and what to do on "fail". Commands marked **NAS** run over SSH on the NAS,
**laptop** on the workstation in `affilio/`.

## 0. What is being deployed

`f8a0418` (deployed 2026-11-04) → `413e2ab`, four commits, 43 files, no dependency or
lockfile change, no web source change (`apps/web` untouched; two shared-type files added):

| Area | Content |
| --- | --- |
| Migrations | `0019_product_ingest` (tables `integration_api_keys`, `product_ingest_batches`, `product_ingest_items`; `products.ingest_batch_id`), `0020_ingest_field_ownership` (`products.ingest_snapshot`, `product_ingest_items.preserved_fields`). Both have working downgrades (drill in `M3.2-B.2-P4B.1-production-compatibility.md`). |
| API | integration-key identity (`Authorization: Bearer afk_…`, scope `products:ingest`), `POST /api/v1/products/ingest`, `GET …/ingest/{batch_id}`, `GET /api/v1/integrations/me`, admin key management, `create_integration_key` CLI. Session login, CSRF and the service token are unchanged. |
| Entrypoint | uvicorn `--forwarded-allow-ips` from `TRUSTED_PROXY_IPS` (was `"*"`). |
| Compose | `api` joins the external network `${AFFILIO_INGEST_NETWORK}` in addition to `default`; every other service stays on `default` only; no new `ports:`. |
| Deploy script | preflight requires `AFFILIO_INGEST_NETWORK` and a non-wildcard `TRUSTED_PROXY_IPS`; the deploy stops before backup and re-tag when the network is missing. |
| Docs/tests | ADRs 0013/0014, runbook rows, new tests. |

Two new `.env` variables are mandatory; `docker compose config` refuses the file without them.

## 1. `TRUSTED_PROXY_IPS` decision

**Value for Gate 1: `172.19.0.0/16`** — the whole `affilio_default` network, nothing wider.

Why this is explicit trust, not blind trust:

- uvicorn checks the **direct TCP peer** only. On a Docker bridge network a peer address in
  `172.19.0.0/16` can only belong to a container attached to `affilio_default`; Docker refuses
  overlapping subnets, so no other network on the NAS can produce such an address.
- Gate 0 verified the membership: the six AFFILIO containers and nothing else. Gate 1 re-checks
  it after the deploy (step 6) and makes it a pass criterion.
- The MeoScout bridge is deliberately **not** in it: it reaches the api only over the ingest
  network, whose subnet is different, so its `X-Forwarded-*` headers are ignored.
- What the trust decides: the address used for the login rate limit and login audit rows, and
  the scheme/host uvicorn reports for forwarded requests. Integration-key requests never use it.

Narrower options considered and why they are not Gate 1 material:

| Option | Why not now |
| --- | --- |
| `/32` of the web container | needs a static address; Docker allows static addresses only on networks created with a user-specified subnet, and `affilio_default` was created from the default pool. It would have to be destroyed and recreated (`compose down`: full outage, new subnet), which is a maintenance-window change, not a deploy. |
| a dedicated web→api network with static IPs | a compose redesign (web on two networks, api on three, `API_INTERNAL_URL` pinned to an IP) plus a subnet-collision risk among the 21 Docker networks on the NAS. Planned as P4C if wanted. |
| `172.19.0.0/24` | identical members today (Docker allocates sequentially from `.2`); no security gain, only false precision. |

## 2. Laptop: sync exactly `413e2ab`

The working tree has one uncommitted change (root `README.md`, whitespace/table formatting;
not part of any image, but it would be recorded as `working_tree=dirty`). Park it first.

```bash
cd ~/AI/aff/affilio
git stash push -m "readme-table-formatting" README.md     # or commit it; either way:
git status --porcelain                                     # PASS: no output
git rev-parse --short=12 HEAD                              # PASS: 413e2ab…
pnpm nas:dry-run                                           # PASS: "Number of deleted files: 0";
                                                           #       file list = the 43 changed files of the diff, nothing else
pnpm nas:sync                                              # PASS: exit 0; prints "synced … revision=413e2ab… working_tree=clean"
```
FAIL on any step: stop; nothing on the NAS has changed except, after `nas:sync`, the source
tree and `.deploy/source-revision` (no container is affected by a sync).

Restore the README change afterwards with `git stash pop` (laptop only).

## 3. NAS: configuration (no container is touched)

```bash
ssh leosunnas
cd /volume1/docker/affilio/infrastructure/docker/synology
cp -p .env /volume1/docker/affilio/backups/env-before-p4b-$(date -u +%Y%m%dT%H%M%SZ)   # keep mode 600
grep -E '^(AFFILIO_INGEST_NETWORK|TRUSTED_PROXY_IPS)=' .env                            # PASS: no output (not set yet)
printf '\n# M3.2-B.2 P4B: MeoScout ingest network and explicit proxy trust\nAFFILIO_INGEST_NETWORK=affilio-ingest\nTRUSTED_PROXY_IPS=172.19.0.0/16\n' >> .env
grep -E '^(AFFILIO_INGEST_NETWORK|TRUSTED_PROXY_IPS)=' .env                            # PASS: both lines, exactly as above
stat -c %a .env                                                                         # PASS: 600
```

Preflight without sudo (parses the compose file with the new `.env`, changes nothing):

```bash
# laptop
NAS_SSH_TTY=0 pnpm nas:deploy check
```
PASS: ends with the preflight summary and exit 0; the log shows
`source on the NAS: revision=413e2ab… working_tree=clean`, no "unset in .env", no
"docker compose config rejected". FAIL: fix `.env`, re-run; nothing else happened.

## 4. NAS: create the ingest network (first NAS change)

```bash
sudo docker network ls --filter name=affilio-ingest --format '{{.Name}}'      # PASS: no output
sudo docker network create --internal affilio-ingest
sudo docker network inspect affilio-ingest -f 'internal={{.Internal}} containers={{len .Containers}}'
                                                                              # PASS: internal=true containers=0
```
This network is outside the compose project; `docker compose down` never removes it.
FAIL (name exists / create error): stop and report; nothing else changed.

## 5. NAS: deploy

```bash
# laptop, interactive (sudo password once)
pnpm nas:deploy deploy
```
What the script does, in order, and what each step means for rollback:

| # | Step | Command the script runs | On failure |
| --- | --- | --- | --- |
| 1 | preflight | `.env` checks, `docker compose config -q` | nothing changed |
| 2 | confirm | `y/N` prompt | nothing changed |
| 3 | ingest network | `docker network inspect affilio-ingest` | nothing changed |
| 4 | backup | `docker exec affilio-postgres pg_dump … --format=custom --compress=6 > backups/pre-deploy-<ts>.dump` | nothing changed |
| 5 | rollback target | `docker tag affilio-api:latest affilio-api:previous` (same for web) | nothing running changed |
| 6 | build | `docker compose -p affilio build --pull api web` | containers untouched; fix and re-run |
| 7 | postgres | `compose up -d postgres`, wait healthy | postgres only |
| 8 | **migrate** | `compose run --rm --no-deps api migrate` (= `alembic upgrade head`, 0018 → 0019 → 0020) | **schema may be partially upgraded: see §7** |
| 9 | up | `compose up -d --remove-orphans` (profile `exports` active: six services; api recreated on both networks, web recreated) | mixed state: §7 |
| 10 | health | every service `running:healthy` within 240 s; `GET /api/v1/health`; `/api/v1/system/info` has `database.status=ok` and `meta.mode=live` | §7 |
| 11 | record | `.deploy/last-deploy` written | — |

PASS: the script prints `deployment complete: deployed_at=… revision=413e2ab… images=…`.

## 6. NAS: post-deploy verification (read-only)

```bash
sudo docker ps --filter label=com.docker.compose.project=affilio --format '{{.Names}} {{.Status}}'
    # PASS: affilio-postgres, -api, -worker, -publisher, -exporter, -web, all "(healthy)"
sudo docker exec affilio-postgres psql -U affilio -d affilio -Atc "select version_num from alembic_version"
    # PASS: 0020_ingest_field_ownership
sudo docker exec affilio-postgres psql -U affilio -d affilio -Atc "select count(*) from products" -c "select count(*) from integration_api_keys"
    # PASS: 0 and 0 (no key exists until Gate 2)
sudo docker network inspect affilio-ingest -f '{{range .Containers}}{{.Name}} {{end}}'
    # PASS: exactly "affilio-api"
sudo docker network inspect affilio_default -f '{{range .Containers}}{{.Name}} {{end}}'
    # PASS: exactly the six affilio-* containers (this is what TRUSTED_PROXY_IPS trusts)
sudo docker exec affilio-api tr '\0' ' ' < /proc/1/cmdline; echo
    # PASS: contains "--forwarded-allow-ips 172.19.0.0/16"
sudo docker port affilio-api
    # PASS: no output (still no published port)
curl -fsS http://<LAN-IP>:3080/api/v1/system/info | head -c 300; echo
    # PASS: "database":{"status":"ok"}, "mode":"live"
```
Then from the laptop browser: log in to the web UI, open a product list page, log out.
PASS: login works (the proxy path and session cookie are unaffected).
Record the output of these commands with the Gate 1 result.

## 7. Rollback (only if Gate 1 fails after step 8 of §5)

**Rule: the old image (`affilio-api:previous`, head 0018) must never start against a 0019/0020
schema.** Its entrypoint runs `alembic upgrade head` and dies with "Can't locate revision
'0020_ingest_field_ownership'". Downgrade first, with the **new** image, then switch images.

`pnpm nas:deploy rollback` enforces this: before it touches any image tag or container it reads
`alembic_version` from the live database and asks `affilio-api:previous` (offline, no network)
for its migration head. It proceeds only when the two are identical. It refuses, changing
nothing, when the database is ahead of or behind that head, when the revision cannot be read
(postgres down, table missing), or when the image cannot report its migrations. It never
downgrades or restores the database itself; that is the operator's explicit step 2 below.

```bash
# NAS, from /volume1/docker/affilio
C="sudo /usr/local/bin/docker compose --project-name affilio -f infrastructure/docker/synology/docker-compose.yml --env-file infrastructure/docker/synology/.env"
# 0. (optional) see the guard refuse: laptop `pnpm nas:deploy rollback` → "database is at
#    '0020_ingest_field_ownership', which affilio-api:previous does not know (its head:
#    0018_upload_root) … Rollback refused, nothing was changed."  PASS: exit 1, no tag/up calls.
# 1. fresh dump (in addition to backups/pre-deploy-<ts>.dump taken in §5 step 4)
sudo /usr/local/bin/docker exec affilio-postgres pg_dump -U affilio -d affilio --format=custom --compress=6 > backups/rollback-$(date +%Y%m%d-%H%M%S).dump
# 2. schema back to 0018 with the NEW image (the only database change, done by you, not the script)
$C run --rm --no-deps api alembic downgrade 0018_upload_root
sudo /usr/local/bin/docker exec affilio-postgres psql -U affilio -d affilio -Atc "select version_num from alembic_version"
    # PASS: 0018_upload_root   (FAIL: go to step 4 instead of 3)
# 3. previous images — the guard now passes ("schema check: database at '0018_upload_root' = head of affilio-api:previous, compatible")
# laptop: pnpm nas:deploy rollback      → answers y; waits for health
# 4. only if the downgrade failed: restore the pre-deploy dump (destructive; stops writers first)
$C stop api worker publisher exporter
scripts/db-restore.sh backups/pre-deploy-<ts>.dump affilio-postgres        # type "restore"
# then step 3
```
Verification after rollback: §6 commands with expected `0018_upload_root`, and the api
command line no longer containing `--forwarded-allow-ips 172.19.0.0/16` (old entrypoint).
The `.env` additions and the ingest network can stay; they are harmless to the old release.

## 8. Not in Gate 1

No integration key is created (Gate 2). The MeoScout bridge is not deployed (Gate 3). No
product is sent (Gate 4). Pushing the commits is a separate decision.
