"""Integration tests of the migration runner, against a real Postgres.

Activated when ``DB_HOST_TEST`` is set (same convention as the other
integration tests). Isolated from the real migrations: a dedicated tracking
table and test tables prefixed ``_cardioanalysis_test_mig_``, all dropped
before and after each test, in the test role's schema (``DB_SCHEMA_TEST``,
default ``cardioanalysis``).
"""

import os
from pathlib import Path

import psycopg2
import pytest

from app.services.migrations import (
    MigrationError,
    MigrationMode,
    migration_status,
    run_migrations,
)

_SCHEMA = os.getenv("DB_SCHEMA_TEST", "cardioanalysis")
_TRACKING = "_cardioanalysis_test_mig_tracking"
_PREFIX = "_cardioanalysis_test_mig_"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("DB_HOST_TEST"),
        reason=(
            "Set DB_HOST_TEST (+ DB_NAME_TEST, DB_USER_TEST, DB_PASSWORD_TEST) "
            "to run integration tests"
        ),
    ),
]


def _connect():
    return psycopg2.connect(
        host=os.environ["DB_HOST_TEST"],
        dbname=os.environ["DB_NAME_TEST"],
        user=os.environ["DB_USER_TEST"],
        password=os.environ["DB_PASSWORD_TEST"],
        port=int(os.environ.get("DB_PORT_TEST", "5432")),
        connect_timeout=10,
    )


def _drop_test_tables(conn) -> None:
    """Drop the tracking table and every test table of the schema."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s "
            "AND tablename LIKE %s",
            (_SCHEMA, _PREFIX.replace("_", r"\_") + "%"),
        )
        for (table,) in cur.fetchall():
            cur.execute(f'DROP TABLE IF EXISTS "{_SCHEMA}"."{table}" CASCADE')
    conn.commit()


def _tables(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s "
            "AND tablename LIKE %s",
            (_SCHEMA, _PREFIX.replace("_", r"\_") + "%"),
        )
        tables = {row[0] for row in cur.fetchall()}
    conn.commit()
    return tables - {_TRACKING}


def _write(directory: Path, name: str, table: str) -> Path:
    path = directory / name
    path.write_text(f"CREATE TABLE {_PREFIX}{table} (id INT);\n", encoding="utf-8")
    return path


@pytest.fixture
def conn():
    """Open a test connection, with clean test tables before and after."""
    connection = _connect()
    _drop_test_tables(connection)
    yield connection
    connection.rollback()
    _drop_test_tables(connection)
    connection.close()


def _run(conn, directory: Path, mode: MigrationMode = MigrationMode.APP):
    return run_migrations(
        conn, schema=_SCHEMA, mode=mode, table=_TRACKING, directory=directory
    )


def test_pending_migrations_are_applied_once(conn, tmp_path: Path) -> None:
    """A first run applies everything, a second one nothing."""
    _write(tmp_path, "A001__first.sql", "t1")
    _write(tmp_path, "A002__second.sql", "t2")

    first = _run(conn, tmp_path)
    second = _run(conn, tmp_path)

    assert [m.version for m in first.applied] == ["A001", "A002"]
    assert second.applied == []
    assert _tables(conn) == {f"{_PREFIX}t1", f"{_PREFIX}t2"}


def test_unqualified_objects_go_to_the_application_schema(conn, tmp_path: Path) -> None:
    """search_path is set to the schema for the migration's transaction."""
    _write(tmp_path, "A001__first.sql", "t1")

    _run(conn, tmp_path)

    assert f"{_PREFIX}t1" in _tables(conn)


def test_failing_migration_is_rolled_back_and_not_recorded(
    conn, tmp_path: Path
) -> None:
    """A failing migration leaves nothing behind; earlier ones stay applied."""
    _write(tmp_path, "A001__first.sql", "t1")
    (tmp_path / "A002__broken.sql").write_text(
        f"CREATE TABLE {_PREFIX}t2 (id INT);\nSELECT * FROM missing_table;\n",
        encoding="utf-8",
    )

    with pytest.raises(psycopg2.Error):
        _run(conn, tmp_path)

    assert _tables(conn) == {f"{_PREFIX}t1"}
    status = migration_status(conn, schema=_SCHEMA, table=_TRACKING, directory=tmp_path)
    assert [(s.migration.version, s.applied_at is not None) for s in status] == [
        ("A001", True),
        ("A002", False),
    ]


def test_run_stops_at_a_privileged_migration(conn, tmp_path: Path) -> None:
    """The application run stops before a privileged migration, keeping order."""
    _write(tmp_path, "A001__first.sql", "t1")
    _write(tmp_path, "A002__hook.privileged.sql", "t2")
    _write(tmp_path, "A003__third.sql", "t3")

    run = _run(conn, tmp_path)

    assert [m.version for m in run.applied] == ["A001"]
    assert run.waiting is not None and run.waiting.version == "A002"
    assert _tables(conn) == {f"{_PREFIX}t1"}


def test_privileged_run_applies_only_privileged_migrations(
    conn, tmp_path: Path
) -> None:
    """Alternating runs apply everything in order, each its own kind."""
    _write(tmp_path, "A001__first.sql", "t1")
    _write(tmp_path, "A002__hook.privileged.sql", "t2")
    _write(tmp_path, "A003__third.sql", "t3")

    _run(conn, tmp_path)
    privileged = _run(conn, tmp_path, MigrationMode.PRIVILEGED)
    last = _run(conn, tmp_path)

    assert [m.version for m in privileged.applied] == ["A002"]
    assert privileged.waiting is not None and privileged.waiting.version == "A003"
    assert [m.version for m in last.applied] == ["A003"]
    assert last.waiting is None


def test_all_mode_applies_both_kinds(conn, tmp_path: Path) -> None:
    """--all applies every migration in one run (disposable databases)."""
    _write(tmp_path, "A001__first.sql", "t1")
    _write(tmp_path, "A002__hook.privileged.sql", "t2")

    run = _run(conn, tmp_path, MigrationMode.ALL)

    assert [m.version for m in run.applied] == ["A001", "A002"]


def test_edited_migration_stops_the_run(conn, tmp_path: Path) -> None:
    """Changing an applied file is refused, before any new migration runs."""
    path = _write(tmp_path, "A001__first.sql", "t1")
    _run(conn, tmp_path)
    path.write_text(f"CREATE TABLE {_PREFIX}other (id INT);\n", encoding="utf-8")
    _write(tmp_path, "A002__second.sql", "t2")

    with pytest.raises(MigrationError, match="A001__first.sql"):
        _run(conn, tmp_path)

    assert _tables(conn) == {f"{_PREFIX}t1"}


def test_status_does_not_create_the_tracking_table(conn, tmp_path: Path) -> None:
    """--status is read-only: on a fresh database everything is pending."""
    _write(tmp_path, "A001__first.sql", "t1")

    status = migration_status(conn, schema=_SCHEMA, table=_TRACKING, directory=tmp_path)

    assert [s.applied_at for s in status] == [None]
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f'"{_SCHEMA}"."{_TRACKING}"',))
        assert cur.fetchone()[0] is None
