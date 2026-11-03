"""Command line: apply the API's database migrations.

Usage::

    python -m app.migrate               # application migrations (DB_USER)
    python -m app.migrate --privileged  # privileged ones, with an admin role
    python -m app.migrate --all         # both, one role: disposable databases only
    python -m app.migrate --status      # list applied and pending migrations

The connection comes from the usual settings (``DB_*``, ``DB_SCHEMA``). For
privileged migrations, point at an env file holding an administrator role:
``ENV_FILE=.env.admin python -m app.migrate --privileged``.
"""

import argparse
import sys

from app.config import DB_SCHEMA
from app.services.db import get_connection
from app.services.migrations import (
    MigrationError,
    MigrationMode,
    migration_status,
    run_migrations,
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.migrate",
        description="Apply the cardioanalysis-api database migrations.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--privileged",
        action="store_true",
        help="apply the privileged migrations (administrator role)",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="apply both kinds with one role (disposable databases only: CI, tests)",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="list the migrations and whether they are applied, change nothing",
    )
    return parser.parse_args(argv)


def _print_status(conn) -> None:
    for status in migration_status(conn, schema=DB_SCHEMA):
        m = status.migration
        state = (
            f"applied {status.applied_at:%Y-%m-%d %H:%M}"
            if status.applied_at
            else "pending"
        )
        kind = " (privileged)" if m.privileged else ""
        print(f"{m.version}  {m.name}{kind}  {state}")


def _apply(conn, mode: MigrationMode) -> None:
    run = run_migrations(conn, schema=DB_SCHEMA, mode=mode)
    for m in run.applied:
        print(f"Applied {m.version}  {m.name}")
    if not run.applied:
        print("No migration to apply.")
    if run.waiting is not None:
        option = " --privileged" if run.waiting.privileged else ""
        print(
            f"Next migration {run.waiting.version} ({run.waiting.name}) needs "
            f"another run: python -m app.migrate{option}"
        )


def main(argv: list[str] | None = None) -> int:
    """Run the command; return the process exit code."""
    args = _parse_args(argv)
    if args.privileged:
        mode = MigrationMode.PRIVILEGED
    elif args.all:
        mode = MigrationMode.ALL
    else:
        mode = MigrationMode.APP

    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001 — any connection failure ends the command
        print(f"Cannot connect to the database: {exc}", file=sys.stderr)
        return 1
    try:
        if args.status:
            _print_status(conn)
        else:
            _apply(conn, mode)
    except MigrationError as exc:
        print(f"Migration error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — report the failing SQL, then exit 1
        print(f"Migration failed and was rolled back: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
