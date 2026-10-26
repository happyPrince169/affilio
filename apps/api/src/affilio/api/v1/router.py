"""Aggregates all v1 routers. Mounted under `settings.api_v1_prefix` by the app factory."""

from __future__ import annotations

from fastapi import APIRouter

from affilio.api.v1.system import system_router
from affilio.modules import iter_module_routers

api_v1_router = APIRouter()
api_v1_router.include_router(system_router)

for module_router in iter_module_routers():
    api_v1_router.include_router(module_router)
