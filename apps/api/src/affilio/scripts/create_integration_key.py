"""Manage integration API keys from the command line (ADR 0013).

    uv run python -m affilio.scripts.create_integration_key create --org demo-org \\
        --name "MeoScout bridge" --scope products:ingest
    uv run python -m affilio.scripts.create_integration_key list --org demo-org
    uv run python -m affilio.scripts.create_integration_key rotate --org demo-org --key <id>
    uv run python -m affilio.scripts.create_integration_key revoke --org demo-org --key <id>

`create` and `rotate` print the plaintext key exactly once, on stdout, and nothing else on
that line, so it can be piped into a secret store. It is never logged or stored. The same
operations exist as admin endpoints under `/api/v1/integrations/keys`.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.core.audit import record_audit
from affilio.db.session import get_session_factory
from affilio.modules.identity import service
from affilio.modules.identity.models import IntegrationApiKey, Organization


def _organization(session: Session, slug: str) -> Organization:
    organization = session.scalar(select(Organization).where(Organization.slug == slug))
    if organization is None:
        print(f"Organization '{slug}' not found.", file=sys.stderr)
        raise SystemExit(2)
    return organization


def _key(session: Session, organization: Organization, raw_id: str) -> IntegrationApiKey:
    try:
        key_id = uuid.UUID(raw_id)
    except ValueError:
        print(f"'{raw_id}' is not a key id.", file=sys.stderr)
        raise SystemExit(2) from None
    return service.get_api_key(session, organization.id, key_id)


def _audit(
    session: Session, organization: Organization, action: str, key: IntegrationApiKey, **data
):
    record_audit(
        session,
        organization_id=organization.id,
        identity=None,
        action=action,
        entity_type="integration_api_key",
        entity_id=key.id,
        data={
            "name": key.name,
            "prefix": key.prefix,
            "scopes": list(key.scopes),
            "via": "cli",
            **data,
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("command", choices=("create", "list", "rotate", "revoke"))
    parser.add_argument("--org", required=True, help="organization slug")
    parser.add_argument("--name", help="create: display name of the integration")
    parser.add_argument(
        "--scope",
        action="append",
        default=[],
        help="create: scope to grant (repeatable); known: "
        + ", ".join(sorted(service.INTEGRATION_SCOPES)),
    )
    parser.add_argument("--expires-in-days", type=int, default=None)
    parser.add_argument("--key", help="rotate/revoke: key id (from `list`)")
    args = parser.parse_args(argv)

    with get_session_factory()() as session:
        organization = _organization(session, args.org)
        if args.command == "list":
            for key in service.list_api_keys(session, organization.id):
                print(
                    f"{key.id}  {key.prefix}…  {service.api_key_state(key):8}  "
                    f"scopes={','.join(key.scopes) or '-'}  last_used={key.last_used_at or '-'}  "
                    f"{key.name}"
                )
            return 0
        if args.command == "create":
            if not args.name:
                print("--name is required for create.", file=sys.stderr)
                return 2
            key, secret = service.create_api_key(
                session,
                organization,
                name=args.name,
                scopes=args.scope,
                created_by=None,
                expires_in_days=args.expires_in_days,
            )
            _audit(session, organization, "integration_key.created", key)
            session.commit()
            print(f"created {key.id} ({key.prefix}…) for {organization.slug}", file=sys.stderr)
            print(secret)
            return 0
        if not args.key:
            print(f"--key is required for {args.command}.", file=sys.stderr)
            return 2
        key = _key(session, organization, args.key)
        if args.command == "revoke":
            service.revoke_api_key(key, revoked_by=None)
            _audit(session, organization, "integration_key.revoked", key)
            session.commit()
            print(f"revoked {key.id} ({key.prefix}…)", file=sys.stderr)
            return 0
        replacement, secret = service.rotate_api_key(session, key, rotated_by=None)
        _audit(
            session,
            organization,
            "integration_key.rotated",
            replacement,
            rotated_from_key_id=str(key.id),
        )
        session.commit()
        print(
            f"rotated {key.id} → {replacement.id} ({replacement.prefix}…); old key revoked",
            file=sys.stderr,
        )
        print(secret)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
