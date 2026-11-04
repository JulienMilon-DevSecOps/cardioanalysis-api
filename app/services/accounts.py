"""Application accounts: one row of ``accounts`` per Supabase Auth user.

The account carries what the JWT does not: the beta status (pending, approved,
rejected, suspended), the role (user, admin) and the display name. It is read
on every authenticated request, so that a status change applies at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from psycopg2 import errors, sql

from app.config import DB_SCHEMA

ACCOUNTS_TABLE = "accounts"
# last_seen_at is written at most once per interval, not on every request
_LAST_SEEN_INTERVAL = "1 minute"

_COLUMNS = (
    "user_id",
    "display_name",
    "status",
    "role",
    "created_at",
    "status_changed_at",
    "onboarding_completed_at",
    "last_seen_at",
)


class AccountStatus(StrEnum):
    """Beta status of an account."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


class AccountRole(StrEnum):
    """Role of an account; only ``accounts.role`` grants admin, never the JWT."""

    USER = "user"
    ADMIN = "admin"


class AccountExistsError(Exception):
    """An account already exists for this user."""


@dataclass(frozen=True)
class Account:
    """One row of ``accounts``."""

    user_id: str
    display_name: str
    status: AccountStatus
    role: AccountRole
    created_at: datetime
    status_changed_at: datetime | None
    onboarding_completed_at: datetime | None
    last_seen_at: datetime | None

    @property
    def onboarding_completed(self) -> bool:
        """Tell whether the welcome screens were completed (on any device)."""
        return self.onboarding_completed_at is not None


class AccountRepository:
    """SQL access to the ``accounts`` table over an open psycopg2 connection.

    Every write is committed at once. The caller owns the connection.
    """

    def __init__(
        self, conn, *, schema: str = DB_SCHEMA, table: str = ACCOUNTS_TABLE
    ) -> None:
        """Bind the repository to a connection and to the table to use."""
        self._conn = conn
        self._table = sql.SQL("{}.{}").format(
            sql.Identifier(schema), sql.Identifier(table)
        )
        self._columns = sql.SQL(", ").join(map(sql.Identifier, _COLUMNS))

    def _fetch_one(self, query: sql.Composable, params: tuple) -> Account | None:
        with self._conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
        self._conn.commit()
        return _to_account(row) if row else None

    def get(self, user_id: str) -> Account | None:
        """Return the account of ``user_id``, or ``None`` if it has none."""
        return self._fetch_one(
            sql.SQL("SELECT {} FROM {} WHERE user_id = %s").format(
                self._columns, self._table
            ),
            (user_id,),
        )

    def list_accounts(self, status: AccountStatus | None = None) -> list[Account]:
        """Return the accounts, oldest first, optionally of one status."""
        query = sql.SQL("SELECT {} FROM {}").format(self._columns, self._table)
        params: tuple = ()
        if status is not None:
            query += sql.SQL(" WHERE status = %s")
            params = (str(status),)
        query += sql.SQL(" ORDER BY created_at, user_id")
        with self._conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        self._conn.commit()
        return [_to_account(row) for row in rows]

    def create(
        self,
        user_id: str,
        display_name: str,
        *,
        status: AccountStatus = AccountStatus.PENDING,
        role: AccountRole = AccountRole.USER,
    ) -> Account:
        """Create the account of ``user_id``.

        A status other than ``pending`` counts as a status change (dated now).

        Raises:
            AccountExistsError: the user already has an account.

        """
        query = sql.SQL(
            "INSERT INTO {} (user_id, display_name, status, role, status_changed_at)"
            " VALUES (%s, %s, %s, %s,"
            " CASE WHEN %s = 'pending' THEN NULL ELSE now() END)"
            " RETURNING {}"
        ).format(self._table, self._columns)
        try:
            account = self._fetch_one(
                query, (user_id, display_name, str(status), str(role), str(status))
            )
        except errors.UniqueViolation as exc:
            self._conn.rollback()
            raise AccountExistsError(user_id) from exc
        assert account is not None  # noqa: S101 — INSERT ... RETURNING always returns
        return account

    def set_role(self, user_id: str, role: AccountRole) -> Account | None:
        """Change the role; return the account, or ``None`` if it does not exist."""
        return self._fetch_one(
            sql.SQL("UPDATE {} SET role = %s WHERE user_id = %s RETURNING {}").format(
                self._table, self._columns
            ),
            (str(role), user_id),
        )

    def update_display_name(self, user_id: str, display_name: str) -> Account | None:
        """Change the display name; return the account (``None`` if missing)."""
        return self._fetch_one(
            sql.SQL(
                "UPDATE {} SET display_name = %s WHERE user_id = %s RETURNING {}"
            ).format(self._table, self._columns),
            (display_name, user_id),
        )

    def complete_onboarding(self, user_id: str) -> Account | None:
        """Record that the welcome screens were seen; the first date is kept."""
        return self._fetch_one(
            sql.SQL(
                "UPDATE {} SET onboarding_completed_at ="
                " COALESCE(onboarding_completed_at, now())"
                " WHERE user_id = %s RETURNING {}"
            ).format(self._table, self._columns),
            (user_id,),
        )

    def touch_last_seen(self, user_id: str) -> None:
        """Update ``last_seen_at``, at most once per minute for a user."""
        query = sql.SQL(
            "UPDATE {} SET last_seen_at = now() WHERE user_id = %s"
            " AND (last_seen_at IS NULL"
            " OR last_seen_at < now() - %s::interval)"
        ).format(self._table)
        with self._conn.cursor() as cur:
            cur.execute(query, (user_id, _LAST_SEEN_INTERVAL))
        self._conn.commit()


def _to_account(row: tuple) -> Account:
    values = dict(zip(_COLUMNS, row, strict=True))
    values["user_id"] = str(values["user_id"])
    values["status"] = AccountStatus(values["status"])
    values["role"] = AccountRole(values["role"])
    return Account(**values)
