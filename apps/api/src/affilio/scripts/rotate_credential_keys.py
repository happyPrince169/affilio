"""`python -m affilio.scripts.rotate_credential_keys` — re-encrypt stored credentials.

Procedure (see docs/architecture/security.md#credential-vault):
1. add the new key to CREDENTIAL_ENCRYPTION_KEYS and point CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID
   at it; restart the API;
2. run this script: every active credential not already under the active key is decrypted
   with its old key and re-encrypted under the active one, in one transaction;
3. once `--check` reports no rows on the old key, remove the old key from the ring.

Never prints key material or tokens.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select

from affilio.core.config import get_settings
from affilio.core.logging import configure_logging
from affilio.db.session import get_session_factory
from affilio.modules.accounts import vault
from affilio.modules.accounts.crypto import get_cipher
from affilio.modules.accounts.models import AccountCredential


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only report rows per key id")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    cipher = get_cipher(settings)
    with get_session_factory()() as session:
        if not args.check:
            rotated = vault.rotate_all(session, cipher, organization_id=None)
            session.commit()
            sys.stdout.write(f"re-encrypted {rotated} credential(s) under {cipher.active_key_id}\n")
        rows = session.execute(
            select(AccountCredential.key_id, func.count())
            .where(AccountCredential.status == "active")
            .group_by(AccountCredential.key_id)
        ).all()
    for key_id, count in rows:
        sys.stdout.write(f"{key_id}: {count} active credential(s)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
