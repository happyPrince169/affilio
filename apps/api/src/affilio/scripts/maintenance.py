"""`python -m affilio.scripts.maintenance` — purge expired sessions and OAuth attempts once.

The worker runs the same routine every MAINTENANCE_INTERVAL_SECONDS; this entry point is for
operators who do not run the worker or want to trigger it from a DSM scheduled task.
"""

from __future__ import annotations

import sys

from affilio.core.config import get_settings
from affilio.core.logging import configure_logging
from affilio.worker.maintenance import run_maintenance


def main() -> int:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    result = run_maintenance(settings)
    sys.stdout.write(
        f"sessions purged: {result['sessions_purged']}, "
        f"oauth attempts purged: {result['oauth_states_purged']}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
