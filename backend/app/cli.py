"""Operational commands.

    python -m app.cli create-admin --email admin@example.edu --name "Grace Hopper"

The password comes from the ADMIN_PASSWORD environment variable or, if that is unset,
an interactive prompt. It is never accepted as an argument, so it does not end up in
shell history or the process list.
"""

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Sequence
from typing import Final

from pydantic import EmailStr, TypeAdapter, ValidationError

from app.core.config import Settings
from app.core.exceptions import DomainError
from app.core.security import PASSWORD_MIN_LENGTH
from app.db.session import create_engine, create_session_factory
from app.schemas.auth import PASSWORD_MAX_LENGTH
from app.services import users

PASSWORD_ENV_VAR: Final = "ADMIN_PASSWORD"  # noqa: S105 - a variable name, not a secret
EXIT_OK: Final = 0
EXIT_ERROR: Final = 1

_email_adapter: TypeAdapter[str] = TypeAdapter(EmailStr)


class CommandError(Exception):
    """A problem to report to the operator, with no traceback."""


def _read_password() -> str:
    password = os.environ.get(PASSWORD_ENV_VAR)
    if password is None:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            raise CommandError("The passwords do not match.")
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise CommandError(
            f"The password must be {PASSWORD_MIN_LENGTH} to {PASSWORD_MAX_LENGTH} characters."
        )
    return password


def _validate_email(email: str) -> str:
    try:
        return _email_adapter.validate_python(email)
    except ValidationError as exc:
        raise CommandError(f"Not a valid email address: {email}") from exc


async def create_admin(
    settings: Settings, *, email: str, full_name: str | None, password: str
) -> str:
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            result = await users.provision_admin(
                session, email=email, password=password, full_name=full_name
            )
            await session.commit()
    finally:
        await engine.dispose()
    action = "Created admin" if result.created else "Promoted to admin and reset the password of"
    return f"{action} {result.user.email}."


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser(
        "create-admin",
        help="Create an admin, or promote an existing account and reset its password.",
    )
    create.add_argument("--email", required=True)
    create.add_argument("--name", help="Full name. Required when the account does not exist.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        email = _validate_email(args.email)
        password = _read_password()
        message = asyncio.run(
            create_admin(Settings(), email=email, full_name=args.name, password=password)
        )
    except (CommandError, DomainError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(message)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
