"""Create or update a user and attach it to an organization (any environment).

    uv run python -m affilio.scripts.create_user --email you@example.com --org demo-org --role admin

The password is read from AFFILIO_NEW_USER_PASSWORD or prompted interactively; it is never
passed on the command line, logged, or printed.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from affilio.core.enums import MembershipRole
from affilio.core.security import MIN_PASSWORD_LENGTH
from affilio.db.session import get_session_factory
from affilio.modules.identity import service
from affilio.modules.identity.models import Organization, User


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--org", required=True, help="organization slug")
    parser.add_argument(
        "--role", default="editor", choices=[r.value for r in MembershipRole if r != "owner"]
    )
    parser.add_argument("--display-name", default=None)
    parser.add_argument("--reset-password", action="store_true", help="update an existing user")
    args = parser.parse_args(argv)

    password = os.environ.get("AFFILIO_NEW_USER_PASSWORD") or getpass.getpass("Password: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
        return 2

    with get_session_factory()() as session, session.begin():
        organization = session.scalar(select(Organization).where(Organization.slug == args.org))
        if organization is None:
            print(f"Organization '{args.org}' not found.", file=sys.stderr)
            return 2
        user = session.scalar(select(User).where(User.email == args.email.strip().lower()))
        if user is None:
            user = service.create_user(
                session, email=args.email, password=password, display_name=args.display_name
            )
            action = "created"
        elif args.reset_password:
            service.set_password(user, password)
            service.revoke_all_sessions(session, user)
            action = "password reset"
        else:
            action = "exists (use --reset-password to change the password)"
        service.add_membership(session, organization, user, MembershipRole(args.role))
    print(f"user {args.email}: {action}; membership {args.role} in {args.org}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
