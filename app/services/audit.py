"""Journal of administration actions (``audit_log`` table).

Entries are written by the account service, in the same transaction as the
change they describe (:func:`insert_entry`), and read by the administration
API (:class:`AuditRepository`). The journal is append only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg2 import sql
from psycopg2.extras import Json

from app.config import DB_SCHEMA

AUDIT_TABLE = "audit_log"

_COLUMNS = ("id", "at", "actor_id", "action", "target", "details")


@dataclass(frozen=True)
class AuditEntry:
    """One row of ``audit_log``."""

    id: int
    at: datetime
    actor_id: str | None
    action: str
    target: str | None
    details: dict


def table(schema: str = DB_SCHEMA, name: str = AUDIT_TABLE) -> sql.Composed:
    """Return the qualified name of the audit table."""
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(name))


def insert_entry(
    cur,
    audit_table: sql.Composable,
    *,
    actor_id: str | None,
    action: str,
    target: str | None,
    details: dict,
) -> None:
    """Add an entry with an open cursor: the caller commits (same transaction)."""
    cur.execute(
        sql.SQL(
            "INSERT INTO {} (actor_id, action, target, details) VALUES (%s, %s, %s, %s)"
        ).format(audit_table),
        (actor_id, action, target, Json(details)),
    )


class AuditRepository:
    """Read access to ``audit_log`` over an open psycopg2 connection."""

    def __init__(
        self, conn, *, schema: str = DB_SCHEMA, name: str = AUDIT_TABLE
    ) -> None:
        """Bind the repository to a connection and to the table to use."""
        self._conn = conn
        self._table = table(schema, name)

    def list_entries(
        self, *, action: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[AuditEntry], int]:
        """Return a page of entries, newest first, and the total count.

        ``action`` filters on one action (e.g. ``account.approve``).
        """
        where = sql.SQL("")
        params: tuple = ()
        if action is not None:
            where = sql.SQL(" WHERE action = %s")
            params = (action,)
        columns = sql.SQL(", ").join(map(sql.Identifier, _COLUMNS))
        with self._conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT count(*) FROM {}{}").format(self._table, where), params
            )
            total = cur.fetchone()[0]
            cur.execute(
                sql.SQL(
                    "SELECT {} FROM {}{} ORDER BY at DESC, id DESC LIMIT %s OFFSET %s"
                ).format(columns, self._table, where),
                (*params, limit, offset),
            )
            rows = cur.fetchall()
        self._conn.commit()
        return [_to_entry(row) for row in rows], total


def _to_entry(row: tuple) -> AuditEntry:
    values = dict(zip(_COLUMNS, row, strict=True))
    if values["actor_id"] is not None:
        values["actor_id"] = str(values["actor_id"])
    return AuditEntry(**values)
