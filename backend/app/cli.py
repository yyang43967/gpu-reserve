"""Operational commands.

    python -m app.cli create-admin --email admin@example.edu --name "Grace Hopper"
    python -m app.cli reset-database    # only with ENVIRONMENT=test
    python -m app.cli refresh-demo [--dry-run]

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
from datetime import UTC, datetime
from typing import Final

from pydantic import EmailStr, TypeAdapter, ValidationError
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import Environment, Settings
from app.core.exceptions import DomainError
from app.core.security import PASSWORD_MIN_LENGTH
from app.db.base import data_tables
from app.db.session import create_engine, create_session_factory
from app.schemas.auth import PASSWORD_MAX_LENGTH
from app.services import demo_plan, demo_refresh, users
from app.services.availability_cache import build_availability_cache

PASSWORD_ENV_VAR: Final = "ADMIN_PASSWORD"  # noqa: S105 - a variable name, not a secret
EXIT_OK: Final = 0
EXIT_ERROR: Final = 1

_email_adapter: TypeAdapter[str] = TypeAdapter(EmailStr)


class CommandError(Exception):
    """A problem to report to the operator, with no traceback."""


def validate_password(password: str) -> str:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise CommandError(
            f"The password must be {PASSWORD_MIN_LENGTH} to {PASSWORD_MAX_LENGTH} characters."
        )
    return password


def read_password(env_var: str = PASSWORD_ENV_VAR) -> str:
    """The password from `env_var`, or an interactive prompt when it is unset."""
    password = os.environ.get(env_var)
    if password is None:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            raise CommandError("The passwords do not match.")
    return validate_password(password)


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


async def reset_database(settings: Settings) -> str:
    """Empty every application table. Refused unless the environment is `test`, so it
    cannot be pointed at development or production data by mistake."""
    if settings.environment is not Environment.TEST:
        raise CommandError("reset-database only runs with ENVIRONMENT=test.")
    names = data_tables()
    tables = ", ".join(f'"{name}"' for name in names)
    engine = create_engine(settings)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()
    return f"Emptied {len(names)} tables."


async def refresh_demo(settings: Settings, *, dry_run: bool, now: datetime) -> str:
    """Book the demo plan again around `now` (app.services.demo_refresh). A dry run
    does all of it in a transaction and rolls back, so it reports exactly what a real
    run would do and changes nothing. A real run invalidates cached availability for
    the servers it changed, like any other write."""
    engine = create_engine(settings)
    cache_redis = Redis.from_url(str(settings.redis_url))
    try:
        factory = create_session_factory(
            engine, availability_cache=build_availability_cache(settings, cache_redis)
        )
        async with factory() as session:
            try:
                result = await demo_refresh.refresh_seeded_reservations(
                    session, now=now, quota_zone=settings.quota_zone
                )
            except (
                demo_refresh.RefreshInProgress,
                demo_refresh.NothingSeeded,
                demo_plan.DemoAdminConflict,
            ) as exc:
                raise CommandError(str(exc)) from exc
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
    finally:
        await cache_redis.aclose()
        await engine.dispose()
    return format_refresh(result, dry_run=dry_run)


def format_refresh(result: demo_refresh.DemoRefresh, *, dry_run: bool) -> str:
    statuses = ", ".join(f"{count} {status}" for status, count in result.by_status.items())
    lines = [
        f"Deleted {result.deleted} seeded reservations and booked {result.created}"
        + (f" ({statuses})." if statuses else ".")
    ]
    if result.skipped:
        lines.append(f"Skipped {len(result.skipped)} plan entries:")
        lines.extend(f"- {entry.purpose}: {entry.reason}" for entry in result.skipped)
    if dry_run:
        lines.append("Dry run: rolled back, nothing was changed.")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser(
        "create-admin",
        help="Create an admin, or promote an existing account and reset its password.",
    )
    create.add_argument("--email", required=True)
    create.add_argument("--name", help="Full name. Required when the account does not exist.")
    commands.add_parser(
        "reset-database",
        help="Empty every table. Only with ENVIRONMENT=test (end-to-end tests).",
    )
    refresh = commands.add_parser(
        "refresh-demo",
        help="Replace the seeded reservations with the demo plan placed around now.",
    )
    refresh.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change, then roll back.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "reset-database":
            message = asyncio.run(reset_database(Settings()))
        elif args.command == "refresh-demo":
            message = asyncio.run(
                refresh_demo(Settings(), dry_run=args.dry_run, now=datetime.now(UTC))
            )
        else:
            email = _validate_email(args.email)
            password = read_password()
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
