"""Application accounts: one row of ``accounts`` per Supabase Auth user.

The account carries what the JWT does not: the beta status (pending, approved,
rejected, suspended), the role (user, admin) and the display name. It is read
on every authenticated request, so that a status change applies at once.

Status changes go through :meth:`AccountRepository.change_status`, which
checks the transition and writes the audit entry in the same transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from psycopg2 import errors, sql

from app.config import DB_SCHEMA
from app.services import audit

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
    "status_changed_by",
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


class AccountAction(StrEnum):
    """Status change requested by an administrator."""

    APPROVE = "approve"
    REJECT = "reject"
    SUSPEND = "suspend"
    REACTIVATE = "reactivate"


# action → (statuses it applies to, resulting status)
TRANSITIONS: dict[AccountAction, tuple[frozenset[AccountStatus], AccountStatus]] = {
    AccountAction.APPROVE: (frozenset({AccountStatus.PENDING}), AccountStatus.APPROVED),
    AccountAction.REJECT: (frozenset({AccountStatus.PENDING}), AccountStatus.REJECTED),
    AccountAction.SUSPEND: (
        frozenset({AccountStatus.APPROVED}),
        AccountStatus.SUSPENDED,
    ),
    AccountAction.REACTIVATE: (
        frozenset({AccountStatus.SUSPENDED}),
        AccountStatus.APPROVED,
    ),
}


class AccountExistsError(Exception):
    """An account already exists for this user."""


class AccountNotFoundError(Exception):
    """The user has no account."""


class OwnAccountError(Exception):
    """An administrator tried to change the status of their own account."""


class InvalidTransitionError(Exception):
    """The action does not apply to the current status of the account."""

    def __init__(self, action: AccountAction, current: AccountStatus) -> None:
        """Keep the action and the current status for the error response."""
        super().__init__(f"Cannot {action} an account that is {current}")
        self.action = action
        self.current = current


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
    status_changed_by: str | None = None

    @property
    def onboarding_completed(self) -> bool:
        """Tell whether the welcome screens were completed (on any device)."""
        return self.onboarding_completed_at is not None


def next_status(
    action: AccountAction, current: AccountStatus, *, user_id: str, actor_id: str
) -> AccountStatus:
    """Return the status an action leads to, after checking it is allowed.

    Raises:
        OwnAccountError: the administrator targets their own account.
        InvalidTransitionError: the action does not apply to ``current``.

    """
    if user_id == actor_id:
        raise OwnAccountError(user_id)
    allowed_from, target = TRANSITIONS[action]
    if current not in allowed_from:
        raise InvalidTransitionError(action, current)
    return target


def escape_like(text: str) -> str:
    r"""Escape ``%``, ``_`` and ``\`` so that ``text`` is matched literally."""
    return text.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


class AccountRepository:
    """SQL access to the ``accounts`` table over an open psycopg2 connection.

    Every write is committed at once (with its audit entry when there is one).
    The caller owns the connection.
    """

    def __init__(
        self,
        conn,
        *,
        schema: str = DB_SCHEMA,
        table: str = ACCOUNTS_TABLE,
        audit_table: str = audit.AUDIT_TABLE,
    ) -> None:
        """Bind the repository to a connection and to the tables to use."""
        self._conn = conn
        self._table = sql.SQL("{}.{}").format(
            sql.Identifier(schema), sql.Identifier(table)
        )
        self._audit_table = audit.table(schema, audit_table)
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
        return self.search(status=status, limit=None)[0]

    def search(
        self,
        *,
        status: AccountStatus | None = None,
        q: str | None = None,
        limit: int | None = 50,
        offset: int = 0,
    ) -> tuple[list[Account], int]:
        """Return a page of accounts, oldest first, and the total count.

        ``q`` matches the display name (anywhere, case insensitive) or the
        start of the user id. ``limit=None`` returns every account.
        """
        conditions: list[sql.Composable] = []
        params: list = []
        if status is not None:
            conditions.append(sql.SQL("status = %s"))
            params.append(str(status))
        if q:
            pattern = escape_like(q.strip())
            conditions.append(
                sql.SQL("(display_name ILIKE %s OR user_id::text ILIKE %s)")
            )
            params += [f"%{pattern}%", f"{pattern}%"]
        where = (
            sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
            if conditions
            else sql.SQL("")
        )
        page = sql.SQL(" LIMIT %s OFFSET %s") if limit is not None else sql.SQL("")
        page_params = [limit, offset] if limit is not None else []
        with self._conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT count(*) FROM {}{}").format(self._table, where), params
            )
            total = cur.fetchone()[0]
            cur.execute(
                sql.SQL("SELECT {} FROM {}{} ORDER BY created_at, user_id{}").format(
                    self._columns, self._table, where, page
                ),
                (*params, *page_params),
            )
            rows = cur.fetchall()
        self._conn.commit()
        return [_to_account(row) for row in rows], total

    def create(
        self,
        user_id: str,
        display_name: str,
        *,
        status: AccountStatus = AccountStatus.PENDING,
        role: AccountRole = AccountRole.USER,
        audit_source: str | None = None,
    ) -> Account:
        """Create the account of ``user_id``.

        A status other than ``pending`` counts as a status change (dated now).
        With ``audit_source`` (e.g. ``"cli"``), an ``account.create`` entry
        without actor is written in the same transaction.

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
            with self._conn.cursor() as cur:
                cur.execute(
                    query, (user_id, display_name, str(status), str(role), str(status))
                )
                account = _to_account(cur.fetchone())
                if audit_source is not None:
                    audit.insert_entry(
                        cur,
                        self._audit_table,
                        actor_id=None,
                        action="account.create",
                        target=user_id,
                        details={
                            "source": audit_source,
                            "status": str(status),
                            "role": str(role),
                        },
                    )
            self._conn.commit()
        except errors.UniqueViolation as exc:
            self._conn.rollback()
            raise AccountExistsError(user_id) from exc
        except Exception:
            self._conn.rollback()
            raise
        return account

    def set_role(
        self, user_id: str, role: AccountRole, *, audit_source: str | None = None
    ) -> Account | None:
        """Change the role; return the account, or ``None`` if it does not exist.

        With ``audit_source``, an ``account.set_role`` entry is written in the
        same transaction (previous and new role).
        """
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT role FROM {} WHERE user_id = %s FOR UPDATE").format(
                        self._table
                    ),
                    (user_id,),
                )
                row = cur.fetchone()
                if row is None:
                    self._conn.rollback()
                    return None
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET role = %s WHERE user_id = %s RETURNING {}"
                    ).format(self._table, self._columns),
                    (str(role), user_id),
                )
                account = _to_account(cur.fetchone())
                if audit_source is not None:
                    audit.insert_entry(
                        cur,
                        self._audit_table,
                        actor_id=None,
                        action="account.set_role",
                        target=user_id,
                        details={
                            "source": audit_source,
                            "from": row[0],
                            "to": str(role),
                        },
                    )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return account

    def change_status(
        self, user_id: str, action: AccountAction, *, actor_id: str
    ) -> Account:
        """Apply an administrator's action and journal it, in one transaction.

        The row is locked while the transition is checked, so that two
        concurrent actions on the same account cannot both succeed.

        Raises:
            AccountNotFoundError: the user has no account.
            OwnAccountError: the administrator targets their own account.
            InvalidTransitionError: the action does not apply to its status.

        """
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT status FROM {} WHERE user_id = %s FOR UPDATE"
                    ).format(self._table),
                    (user_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise AccountNotFoundError(user_id)
                current = AccountStatus(row[0])
                target = next_status(
                    action, current, user_id=user_id, actor_id=actor_id
                )
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET status = %s, status_changed_at = now(),"
                        " status_changed_by = %s WHERE user_id = %s RETURNING {}"
                    ).format(self._table, self._columns),
                    (str(target), actor_id, user_id),
                )
                account = _to_account(cur.fetchone())
                audit.insert_entry(
                    cur,
                    self._audit_table,
                    actor_id=actor_id,
                    action=f"account.{action}",
                    target=user_id,
                    details={"from": str(current), "to": str(target)},
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return account

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
    if values["status_changed_by"] is not None:
        values["status_changed_by"] = str(values["status_changed_by"])
    return Account(**values)
