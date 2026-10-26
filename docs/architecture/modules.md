# Domain modules

All modules live in `apps/api/src/affilio/modules/`. Each is a Python package with, as it
matures, the following files:

```
modules/<name>/
├── __init__.py     docstring: purpose + milestone
├── models.py       SQLAlchemy models (tables owned by this module)
├── schemas.py      Pydantic request/response models
├── service.py      business logic; the ONLY entry point for other modules
├── repository.py   (optional) query helpers
└── router.py       FastAPI router, prefix /<name>, mounted under /api/v1
```

Every module exposes `router` (possibly empty). `affilio.modules.iter_module_routers()`
mounts them in order; `DOMAIN_MODULES` is also what `/api/v1/system/info` reports.

## Dependency direction

Modules are ordered. A module may depend on modules **to its left**, never to its right.

```
identity → accounts → media → products → content → publishing → analytics → agents
```

| Module       | Owns                                                        | May depend on                     | Milestone |
| ------------ | ----------------------------------------------------------- | --------------------------------- | --------- |
| `identity`   | organizations, users, memberships, auth, RBAC               | core, db                          | M0 tables; auth later |
| `accounts`   | social/marketplace accounts, (later) encrypted credentials  | identity                          | M0 table; connect flows M3 |
| `media`      | NAS media index, metadata (FFmpeg), tags, usage tracking    | identity                          | M1        |
| `products`   | affiliate product catalog, links, research notes            | identity, media                   | M2        |
| `content`    | platform-agnostic content items, variants, calendar states  | identity, accounts, media, products | M3      |
| `publishing` | official platform clients, publish jobs, manual checklists  | identity, accounts, content       | M3        |
| `analytics`  | performance imports, attribution, commission reporting      | identity, accounts, content, products | M4    |
| `agents`     | Claude tool-based assistant, approvals, run audit trail     | everything before it              | M5        |

Cross-cutting code (`affilio/core`, `affilio/db`, `affilio/api`) is not a module and may be
used by any module. Modules never import `affilio.api`.

## Rules

1. **Tables belong to one module.** Another module reads them only through that module's
   service functions (or a read-model it exposes), never by importing its models into queries.
   Importing a model class for a foreign key or relationship type is fine.
2. **Services take `organization_id`.** Every service function on tenant data requires it
   and applies it in the query. This is where organization isolation is enforced.
3. **Routers are thin.** Validate with Pydantic, call a service, return a schema.
4. **No platform calls outside `publishing`/`analytics`.** And only official APIs there.
5. **Agents act through tools that call services.** Any tool that mutates or publishes
   returns a proposal that requires human approval before execution.

## Frontend mirror

```
apps/web/src/
├── app/(dashboard)/<area>/page.tsx   route per navigation item
├── components/<area>/                feature components
├── components/layout/                shell: sidebar, top nav, page header
├── components/ui/                    shadcn/ui primitives (generated, formatted by Prettier)
├── lib/api/<area>.ts                 fetchers + TanStack `queryOptions`
└── config/                           navigation, milestones, site metadata
```

`packages/shared-types` mirrors the API's Pydantic schemas and enums. Change both sides in
the same commit.
