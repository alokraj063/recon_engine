"""
Create, re-password, activate or deactivate a login. Run from backend/:

    ../.venv/bin/python scripts/create_user.py --list
    ../.venv/bin/python scripts/create_user.py -e me@example.com -n "My Name"
    ../.venv/bin/python scripts/create_user.py -e me@example.com --password-stdin
    ../.venv/bin/python scripts/create_user.py -e old@example.com --deactivate

With no --password the password is prompted for twice and never echoed;
--password-stdin reads it from a pipe, which is how a deploy job should
feed it (an argv password is visible in `ps` and in shell history, so
there is deliberately no --password <value> flag).

Creating a user is an explicit act on purpose — no migration ships a
default account, and an empty users table means nobody can sign in. For
an unattended first boot, set ADMIN_EMAIL / ADMIN_PASSWORD instead and
let db/seeds.py create the same row (see seed_admin_user).
"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from db import SessionLocal  # noqa: E402
from db.models import User  # noqa: E402
from passwords import (PasswordError, hash_password, normalize_email,  # noqa: E402
                       validate_password)


def prompt_password() -> str:
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("passwords did not match")
    return first


def list_users() -> int:
    with SessionLocal() as session:
        rows = session.execute(
            select(User).order_by(User.email)).scalars().all()
        if not rows:
            print("no users — nobody can sign in yet")
            return 0
        width = max(len(r.email) for r in rows)
        for row in rows:
            last = row.last_login_at.strftime("%Y-%m-%d %H:%M") \
                if row.last_login_at else "never"
            print(f"{row.email:<{width}}  {'active' if row.is_active else 'DISABLED':<8}  "
                  f"last login {last}  {row.name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show every login and exit")
    ap.add_argument("-e", "--email")
    ap.add_argument("-n", "--name", help="display name (defaults to the email)")
    ap.add_argument("--password-stdin", action="store_true",
                    help="read the password from stdin instead of prompting")
    ap.add_argument("--deactivate", action="store_true",
                    help="revoke access (takes effect on the next request)")
    ap.add_argument("--activate", action="store_true", help="restore access")
    args = ap.parse_args()

    if args.list:
        return list_users()
    if not args.email:
        ap.error("--email is required (or use --list)")

    email = normalize_email(args.email)

    with SessionLocal() as session:
        row = session.execute(
            select(User).where(User.email == email)).scalar_one_or_none()

        if args.deactivate or args.activate:
            if row is None:
                raise SystemExit(f"no such user: {email}")
            row.is_active = args.activate
            session.commit()
            print(f"{email}: {'active' if row.is_active else 'DISABLED'}")
            return 0

        if args.password_stdin:
            password = sys.stdin.read().strip()
            if not password:
                raise SystemExit("no password on stdin")
        else:
            password = prompt_password()

        try:
            validate_password(password)
            hashed = hash_password(password)
        except PasswordError as exc:
            raise SystemExit(str(exc))

        if row is None:
            session.add(User(email=email, name=args.name or email,
                             password_hash=hashed))
            action = "created"
        else:
            row.password_hash = hashed
            if args.name:
                row.name = args.name
            # a password reset is also how a locked-out account comes back
            row.is_active = True
            action = "password updated for"
        session.commit()

    print(f"{action} {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
