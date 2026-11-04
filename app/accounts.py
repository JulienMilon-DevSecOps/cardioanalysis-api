"""Command line: manage application accounts outside the API.

For the first administrator of an environment, and as a fallback. No API route
can grant the admin role: it is given here, by someone who already has access
to the database (the usual ``DB_*`` settings, ``ENV_FILE`` to pick another
backend).

Usage::

    python -m app.accounts create <user_id> --display-name Juju --status approved --role admin
    python -m app.accounts set-role <user_id> admin
    python -m app.accounts list [--status pending]

``<user_id>`` is the Supabase Auth user id (``auth.users.id``). Status changes
of everyday use go through the administration API, not through this command.
"""

import argparse
import sys
import uuid

from app.services.accounts import (
    Account,
    AccountExistsError,
    AccountRepository,
    AccountRole,
    AccountStatus,
)
from app.services.db import get_connection


def _user_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a user id (UUID): {value}") from exc


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.accounts",
        description="Manage cardioanalysis-api accounts (first admin, fallback).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create the account of a user")
    create.add_argument("user_id", type=_user_id, help="Supabase Auth user id")
    create.add_argument("--display-name", required=True, help="first name or nickname")
    create.add_argument(
        "--status",
        choices=[s.value for s in AccountStatus],
        default=AccountStatus.PENDING.value,
    )
    create.add_argument(
        "--role", choices=[r.value for r in AccountRole], default=AccountRole.USER.value
    )

    set_role = commands.add_parser("set-role", help="make a user admin, or not")
    set_role.add_argument("user_id", type=_user_id, help="Supabase Auth user id")
    set_role.add_argument("role", choices=[r.value for r in AccountRole])

    listing = commands.add_parser("list", help="list the accounts (no health data)")
    listing.add_argument("--status", choices=[s.value for s in AccountStatus])

    return parser.parse_args(argv)


def _format(account: Account) -> str:
    last_seen = (
        f"{account.last_seen_at:%Y-%m-%d %H:%M}" if account.last_seen_at else "never"
    )
    return (
        f"{account.user_id}  {account.display_name}  {account.status}  "
        f"{account.role}  created {account.created_at:%Y-%m-%d}  last seen {last_seen}"
    )


def _run(accounts: AccountRepository, args: argparse.Namespace) -> int:
    if args.command == "create":
        display_name = args.display_name.strip()
        if not 1 <= len(display_name) <= 50:
            print("The display name must be 1 to 50 characters long.", file=sys.stderr)
            return 1
        try:
            account = accounts.create(
                args.user_id,
                display_name,
                status=AccountStatus(args.status),
                role=AccountRole(args.role),
            )
        except AccountExistsError:
            print(f"An account already exists for {args.user_id}.", file=sys.stderr)
            return 1
        print(f"Created: {_format(account)}")
    elif args.command == "set-role":
        account = accounts.set_role(args.user_id, AccountRole(args.role))
        if account is None:
            print(f"No account for {args.user_id}.", file=sys.stderr)
            return 1
        print(f"Updated: {_format(account)}")
    else:
        status = AccountStatus(args.status) if args.status else None
        for account in accounts.list_accounts(status):
            print(_format(account))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command; return the process exit code."""
    args = _parse_args(argv)
    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001 — any connection failure ends the command
        print(f"Cannot connect to the database: {exc}", file=sys.stderr)
        return 1
    try:
        return _run(AccountRepository(conn), args)
    except Exception as exc:  # noqa: BLE001 — e.g. table missing: migrations not applied
        print(f"Database error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
