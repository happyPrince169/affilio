"""Domain modules of the modular monolith.

Each module owns its models, schemas, services, and router. Modules may depend on modules
listed *before* them in `DOMAIN_MODULES`; never on modules listed after. Cross-module access
goes through the owning module's service layer, not through its tables.
See docs/architecture/modules.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from importlib import import_module

from fastapi import APIRouter

DOMAIN_MODULES: tuple[str, ...] = (
    "identity",
    "accounts",
    "media",
    "products",
    "procurement",
    "production",
    "content",
    "publishing",
    "analytics",
    "agents",
)


def iter_module_routers() -> Iterator[APIRouter]:
    """Yield each module's `router` (modules without one are skipped)."""
    for name in DOMAIN_MODULES:
        module = import_module(f"affilio.modules.{name}.router")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            yield router
