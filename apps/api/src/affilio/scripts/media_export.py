"""Queue, inspect or cancel a publish-ready export of ONE final video (Export pipeline E1).

    uv run python -m affilio.scripts.media_export queue  --asset <uuid> [--org slug] [--run]
    uv run python -m affilio.scripts.media_export status --job <uuid> | --asset <uuid> [--org slug]
    uv run python -m affilio.scripts.media_export cancel --job <uuid> [--org slug]

`queue` only records a job; the export worker (`python -m affilio.worker.exporter`) renders
it. `--run` executes the job synchronously in this process for development and tests; it is
refused when APP_ENV is production unless `--allow-production` is given explicitly, so nothing
transcodes on the NAS by accident. There is no bulk mode on purpose (E1).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

from sqlalchemy import select

from affilio.core.config import get_settings
from affilio.core.enums import ExportTrigger
from affilio.core.errors import AppError
from affilio.core.logging import configure_logging
from affilio.db.session import get_session_factory
from affilio.modules.identity.models import Organization
from affilio.modules.media import exports
from affilio.modules.media.models import MediaExportJob
from affilio.modules.media.roots import MediaRootRegistry


def _job_summary(job: MediaExportJob) -> dict:
    return {
        "id": str(job.id),
        "asset_id": str(job.media_asset_id),
        "status": job.status.value,
        "trigger": job.trigger.value,
        "recipe_key": job.recipe_key,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "error_code": job.error_code,
        "error": job.error,
        "output_variant_id": str(job.output_variant_id) if job.output_variant_id else None,
        "stats": job.stats,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish-ready export of one final video.")
    parser.add_argument("--org", default=None, help="organization slug (default from settings)")
    sub = parser.add_subparsers(dest="command", required=True)
    queue = sub.add_parser("queue", help="queue an export job for one final")
    queue.add_argument("--asset", required=True, type=uuid.UUID)
    queue.add_argument("--max-attempts", type=int, default=None)
    queue.add_argument("--run", action="store_true", help="run the job now in this process")
    queue.add_argument(
        "--allow-production",
        action="store_true",
        help="required with --run when APP_ENV=production (otherwise refused)",
    )
    status = sub.add_parser("status", help="show a job or the jobs of one asset")
    status.add_argument("--job", type=uuid.UUID)
    status.add_argument("--asset", type=uuid.UUID)
    cancel = sub.add_parser("cancel", help="cancel a queued or running job")
    cancel.add_argument("--job", required=True, type=uuid.UUID)
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    # Production guard first, before any database access: a bare --run never transcodes on
    # the NAS by accident; the exporter worker is the production path.
    if (
        args.command == "queue"
        and args.run
        and settings.app_env == "production"
        and not args.allow_production
    ):
        print(
            "Refusing --run in production without --allow-production; queue the job and let "
            "the exporter worker render it.",
            file=sys.stderr,
        )
        return 2
    slug = args.org or settings.default_organization_slug
    session_factory = get_session_factory()

    with session_factory() as session:
        organization = session.scalar(select(Organization).where(Organization.slug == slug))
        if organization is None:
            print(f"Organization '{slug}' not found.", file=sys.stderr)
            return 2
        try:
            if args.command == "queue":
                job = exports.enqueue_export(
                    session,
                    organization.id,
                    args.asset,
                    trigger=ExportTrigger.CLI,
                    requested_by="cli",
                    max_attempts=args.max_attempts or settings.export_max_attempts,
                )
                session.commit()
                if args.run:
                    registry = MediaRootRegistry.from_settings(settings)
                    claimed = exports.claim_next_export_job(session, worker_id="cli")
                    if claimed is None or claimed.id != job.id:
                        print("Another worker claimed the job; see `status`.", file=sys.stderr)
                        return 1
                    session.commit()
                    generator = exports.ExportGenerator(settings, registry, worker_id="cli")
                    job = generator.run(session, claimed)
                print(json.dumps(_job_summary(job), indent=2))
                return 0 if job.status.value in {"queued", "ready", "skipped"} else 1
            if args.command == "status":
                if args.job:
                    jobs = [exports.get_export_job(session, organization.id, args.job)]
                elif args.asset:
                    jobs = exports.list_export_jobs(session, organization.id, asset_id=args.asset)
                else:
                    print("status needs --job or --asset", file=sys.stderr)
                    return 2
                print(json.dumps([_job_summary(j) for j in jobs], indent=2))
                return 0
            if args.command == "cancel":
                job = exports.cancel_export(session, organization.id, args.job, actor="cli")
                session.commit()
                print(json.dumps(_job_summary(job), indent=2))
                return 0
        except AppError as exc:
            print(f"{exc.code}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
