"""`python -m affilio.worker` — run the background worker loop.

`python -m affilio.worker --healthcheck` exits 0 when the heartbeat file is fresh (used by the
container healthcheck).
"""

from __future__ import annotations

import sys

from affilio.core.config import get_settings
from affilio.worker.runner import heartbeat_is_fresh, run_forever


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--healthcheck" in argv:
        return 0 if heartbeat_is_fresh(get_settings()) else 1
    run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
