"""Run a media scan synchronously from the command line (no worker needed).

    uv run python -m affilio.scripts.media_scan [--org demo-org] [--full] [--thumbnails]

Useful on the workstation and for one-off operations on the NAS. Prints a stats summary.
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from affilio.core.config import get_settings
from affilio.core.enums import ScanJobStatus, ScanTrigger
from affilio.core.logging import configure_logging
from affilio.db.session import get_session_factory
from affilio.modules.identity.models import Organization
from affilio.modules.media import service
from affilio.modules.media.roots import MediaRootRegistry
from affilio.modules.media.thumbnails import ThumbnailGenerator
from affilio.worker.runner import claim_next_scan_job, execute_scan_job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan the originals media root now.")
    parser.add_argument("--org", default=None, help="organization slug (default from settings)")
    parser.add_argument("--full", action="store_true", help="re-fingerprint and re-probe all files")
    parser.add_argument("--thumbnails", action="store_true", help="also render pending thumbnails")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    registry = MediaRootRegistry.from_settings(settings)
    slug = args.org or settings.default_organization_slug
    session_factory = get_session_factory()

    with session_factory() as session:
        organization = session.scalar(select(Organization).where(Organization.slug == slug))
        if organization is None:
            print(f"Organization '{slug}' not found. Run the seed first.", file=sys.stderr)
            return 2
        try:
            job = service.enqueue_scan(
                session,
                organization.id,
                registry,
                trigger=ScanTrigger.CLI,
                requested_by="cli",
                full_rescan=args.full,
                max_attempts=1,
            )
        except Exception as exc:
            print(f"Cannot start scan: {exc}", file=sys.stderr)
            return 2
        session.commit()
        claimed = claim_next_scan_job(session)
        if claimed is None or claimed.id != job.id:
            print("Another worker claimed the job; check scan history.", file=sys.stderr)
            return 1
        session.commit()
        job = execute_scan_job(session, claimed, settings=settings, registry=registry)
        print(f"scan {job.status} (attempt {job.attempt})")
        print(json.dumps(job.stats, indent=2))
        if job.errors:
            print(f"{len(job.errors)} error(s); first: {job.errors[0]}")
        if job.status != ScanJobStatus.SUCCEEDED:
            print(f"error: {job.error}", file=sys.stderr)
            return 1

    if args.thumbnails:
        generator = ThumbnailGenerator(settings, registry)
        total = 0
        while True:
            processed = generator.process_pending(session_factory, limit=8)
            total += processed
            if processed == 0:
                break
        print(f"thumbnails processed: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
