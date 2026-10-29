"""Periodic housekeeping shared by the worker loop and the `maintenance` CLI (M3B.1A).

- expired / revoked user sessions older than `SESSION_RETENTION_DAYS` are deleted;
- finished or expired OAuth attempts older than `OAUTH_STATE_RETENTION_HOURS` are deleted;
- idle editor upload sessions past their TTL are expired and their staging copies removed,
  together with orphan `.part` files in the staging directory (upload track U4, only where
  the finals root is writable, i.e. the worker container).

Nothing here touches finals, originals, proxies, credentials or publications.
"""

from __future__ import annotations

from datetime import timedelta

import structlog

from affilio.core.config import Settings, get_settings
from affilio.db.session import get_session_factory
from affilio.modules.accounts import connections as connections_service
from affilio.modules.identity import service as identity_service
from affilio.modules.media.roots import MediaRootRegistry
from affilio.modules.media.upload_finalize import expire_and_clean

log = structlog.get_logger("affilio.worker.maintenance")


def run_maintenance(settings: Settings | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        sessions = identity_service.purge_sessions(
            session, retention=timedelta(days=settings.session_retention_days)
        )
        states = connections_service.purge_oauth_states(
            session, retention=timedelta(hours=settings.oauth_state_retention_hours)
        )
        session.commit()
        uploads = expire_and_clean(session, MediaRootRegistry.from_settings(settings), settings)
    result = {"sessions_purged": sessions, "oauth_states_purged": states}
    result.update({f"uploads_{k}": v for k, v in uploads.items()})
    log.info("worker.maintenance", **result)
    return result
