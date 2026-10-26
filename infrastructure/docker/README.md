# infrastructure/docker

Container assets referenced by the root `docker-compose.yml`.

- `postgres/init/` — scripts mounted into `/docker-entrypoint-initdb.d`. They run **only** when
  the data volume is empty (first start). `pnpm db:reset` wipes the volume and re-runs them.

Planned for later milestones (not present yet): API and web images, a Redis service if a
background-processing need is confirmed, and NAS mount configuration for the media library.
