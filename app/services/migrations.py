"""Database migrations of the API's own tables.

The API's tables (accounts, consents, features...) are versioned by the SQL
files of ``app/migrations/``, distinct from cardiolab's ``V<nnn>`` migrations
(which cardiolab applies itself and tracks in its own ``schema_migrations``
table). This module discovers those files and applies the pending ones, in
order, recording each one in a tracking table of the application schema.

File names: ``A<nnn>__<description>.sql`` (e.g. ``A001__accounts.sql``).
A migration that needs an administrator role (objects in the ``auth`` schema,
such as a trigger on ``auth.users``) is named
``A<nnn>__<description>.privileged.sql``: it is never applied by the
application role. Both kinds share one numbering, so the order is kept: a run
stops at the first pending migration of the other kind.

Each migration runs in its own transaction with its tracking row: if it fails,
nothing of it is kept and the error propagates. A migration already applied
must never be edited: its checksum is recorded and checked on every run.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from psycopg2 import sql

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
TRACKING_TABLE = "api_schema_migrations"

_FILENAME_RE = re.compile(
    r"^A(?P<number>\d{3})__(?P<name>[a-z0-9_]+)(?P<priv>\.privileged)?\.sql$"
)
# Serialises concurrent runs (several containers starting at once). A
# transaction-level lock, so it also works through a pooler in transaction mode.
_ADVISORY_LOCK_KEY = 4_163_720_301


class MigrationError(Exception):
    """Invalid migration files, or a migration edited after being applied."""


class MigrationMode(StrEnum):
    """Which migrations a run is allowed to apply."""

    APP = "app"
    PRIVILEGED = "privileged"
    # Both kinds with a single role: disposable databases only (CI, tests)
    ALL = "all"


@dataclass(frozen=True)
class Migration:
    """One migration file."""

    version: str
    name: str
    privileged: bool
    path: Path

    @property
    def sql(self) -> str:
        """Return the SQL text of the migration."""
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        """Return the SHA-256 of the file, recorded when it is applied."""
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def allowed_in(self, mode: MigrationMode) -> bool:
        """Tell whether a run in ``mode`` may apply this migration."""
        if mode is MigrationMode.ALL:
            return True
        return self.privileged == (mode is MigrationMode.PRIVILEGED)


@dataclass
class MigrationRun:
    """Outcome of :func:`run_migrations`."""

    applied: list[Migration] = field(default_factory=list)
    # First pending migration this run was not allowed to apply (other kind)
    waiting: Migration | None = None


@dataclass(frozen=True)
class MigrationStatus:
    """A migration file and when it was applied (``None``: pending)."""

    migration: Migration
    applied_at: datetime | None


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """List the migration files of ``directory``, in application order.

    Files other than ``*.sql`` (e.g. a README) are ignored.

    Raises:
        MigrationError: a ``.sql`` file is badly named, or two files share
            the same number.

    """
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if match is None:
            raise MigrationError(
                f"Invalid migration file name: {path.name} "
                "(expected A<nnn>__<description>[.privileged].sql)"
            )
        migrations.append(
            Migration(
                version=f"A{match['number']}",
                name=match["name"],
                privileged=match["priv"] is not None,
                path=path,
            )
        )
    versions = [m.version for m in migrations]
    duplicates = sorted({v for v in versions if versions.count(v) > 1})
    if duplicates:
        raise MigrationError(f"Duplicate migration numbers: {', '.join(duplicates)}")
    return migrations


def _table(schema: str, table: str) -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))


def _tracking_table_exists(cur, schema: str, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f'"{schema}"."{table}"',))
    return cur.fetchone()[0] is not None


def _ensure_tracking_table(conn, schema: str, table: str) -> None:
    """Create the schema and the tracking table if they do not exist yet.

    Existence is checked first: ``CREATE ... IF NOT EXISTS`` still requires the
    CREATE privilege, which a role that only uses the table may not have.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,))
        if cur.fetchone() is None:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        if not _tracking_table_exists(cur, schema, table):
            cur.execute(
                sql.SQL(
                    "CREATE TABLE {} ("
                    " version    TEXT        PRIMARY KEY,"
                    " name       TEXT        NOT NULL,"
                    " privileged BOOLEAN     NOT NULL,"
                    " checksum   TEXT        NOT NULL,"
                    " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                ).format(_table(schema, table))
            )
    conn.commit()


def _applied(conn, schema: str, table: str) -> dict[str, tuple[str, datetime]]:
    """Return ``{version: (checksum, applied_at)}`` of the applied migrations."""
    with conn.cursor() as cur:
        if not _tracking_table_exists(cur, schema, table):
            return {}
        cur.execute(
            sql.SQL("SELECT version, checksum, applied_at FROM {}").format(
                _table(schema, table)
            )
        )
        rows = cur.fetchall()
    conn.commit()
    return {version: (checksum, applied_at) for version, checksum, applied_at in rows}


def _check_unchanged(
    migrations: list[Migration], applied: dict[str, tuple[str, datetime]]
) -> None:
    """Refuse to go on if an applied migration was edited afterwards."""
    edited = [
        m.path.name
        for m in migrations
        if m.version in applied and applied[m.version][0] != m.checksum
    ]
    if edited:
        raise MigrationError(
            "Migrations edited after being applied (write a new migration "
            f"instead): {', '.join(edited)}"
        )


def migration_status(
    conn,
    *,
    schema: str,
    table: str = TRACKING_TABLE,
    directory: Path = MIGRATIONS_DIR,
) -> list[MigrationStatus]:
    """Return every migration file with its application date, if applied.

    Read-only: nothing is created when the tracking table does not exist yet.
    """
    migrations = discover_migrations(directory)
    applied = _applied(conn, schema, table)
    _check_unchanged(migrations, applied)
    return [
        MigrationStatus(m, applied[m.version][1] if m.version in applied else None)
        for m in migrations
    ]


def run_migrations(
    conn,
    *,
    schema: str,
    mode: MigrationMode = MigrationMode.APP,
    table: str = TRACKING_TABLE,
    directory: Path = MIGRATIONS_DIR,
) -> MigrationRun:
    """Apply the pending migrations, in order, as far as ``mode`` allows.

    Unqualified objects of a migration are created in ``schema``
    (``search_path`` set for its transaction).

    Args:
        conn: Open ``psycopg2`` connection; the caller closes it.
        schema: Application schema, which holds the tracking table.
        mode: Kind of migrations this run may apply (see :class:`MigrationMode`).
        table: Tracking table name (tests use an isolated one).
        directory: Folder of the migration files.

    Returns:
        The migrations applied, and the first pending one left for a run in
        another mode, if any.

    Raises:
        MigrationError: invalid files, or an applied migration was edited.
        psycopg2.Error: a migration failed; it was rolled back and the
            migrations before it stay applied.

    """
    migrations = discover_migrations(directory)
    _ensure_tracking_table(conn, schema, table)
    applied = _applied(conn, schema, table)
    _check_unchanged(migrations, applied)

    run = MigrationRun()
    for migration in (m for m in migrations if m.version not in applied):
        if not migration.allowed_in(mode):
            run.waiting = migration
            break
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (_ADVISORY_LOCK_KEY,))
                cur.execute(
                    sql.SQL("SELECT 1 FROM {} WHERE version = %s").format(
                        _table(schema, table)
                    ),
                    (migration.version,),
                )
                if cur.fetchone() is not None:
                    # Applied meanwhile by a concurrent run
                    conn.commit()
                    continue
                cur.execute(
                    sql.SQL("SET LOCAL search_path TO {}, public").format(
                        sql.Identifier(schema)
                    )
                )
                cur.execute(migration.sql)
                cur.execute(
                    sql.SQL(
                        "INSERT INTO {} (version, name, privileged, checksum) "
                        "VALUES (%s, %s, %s, %s)"
                    ).format(_table(schema, table)),
                    (
                        migration.version,
                        migration.name,
                        migration.privileged,
                        migration.checksum,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        run.applied.append(migration)
    return run
