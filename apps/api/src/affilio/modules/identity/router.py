"""identity HTTP routes. Intentionally empty in M0."""

from fastapi import APIRouter

router = APIRouter(prefix="/identity", tags=["identity"])
