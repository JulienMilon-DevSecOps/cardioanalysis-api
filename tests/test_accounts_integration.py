"""Integration tests of ``AccountRepository`` against a real Postgres.

Activated when ``DB_HOST_TEST`` is set. The real ``accounts`` table is never
touched: an isolated ``_cardioanalysis_test_accounts`` table is built from the
real migration (``A001__accounts.sql``), before and after each test dropped.
"""

import os
import uuid

import psycopg2
import pytest

from app.services.accounts import (
    AccountExistsError,
    AccountRepository,
    AccountRole,
    AccountStatus,
)
from app.services.migrations import MIGRATIONS_DIR

_SCHEMA = os.getenv("DB_SCHEMA_TEST", "cardioanalysis")
_TABLE = "_cardioanalysis_test_accounts"

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


def _drop(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS "{_SCHEMA}"."{_TABLE}"')
    conn.commit()


@pytest.fixture
def conn():
    """Open a connection with the isolated table built from the real migration."""
    connection = _connect()
    _drop(connection)
    migration = (MIGRATIONS_DIR / "A001__accounts.sql").read_text(encoding="utf-8")
    with connection.cursor() as cur:
        cur.execute(f'SET search_path TO "{_SCHEMA}"')
        cur.execute(migration.replace("accounts", _TABLE))
    connection.commit()
    yield connection
    connection.rollback()
    _drop(connection)
    connection.close()


@pytest.fixture
def repo(conn) -> AccountRepository:
    """Repository bound to the isolated table."""
    return AccountRepository(conn, schema=_SCHEMA, table=_TABLE)


def _set_last_seen(conn, user_id: str, interval: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f'UPDATE "{_SCHEMA}"."{_TABLE}" '  # noqa: S608 — test table name
            "SET last_seen_at = now() - %s::interval WHERE user_id = %s",
            (interval, user_id),
        )
    conn.commit()


def test_create_and_get(repo: AccountRepository) -> None:
    """A created account is read back with the database defaults."""
    user_id = str(uuid.uuid4())

    created = repo.create(user_id, "Test")
    read = repo.get(user_id)

    assert read == created
    assert read.status == AccountStatus.PENDING
    assert read.role == AccountRole.USER
    assert read.status_changed_at is None
    assert read.onboarding_completed is False


def test_creating_with_a_status_dates_the_change(repo: AccountRepository) -> None:
    """A status other than pending at creation counts as a status change."""
    account = repo.create(
        str(uuid.uuid4()),
        "Admin",
        status=AccountStatus.APPROVED,
        role=AccountRole.ADMIN,
    )

    assert account.status_changed_at is not None
    assert account.role == AccountRole.ADMIN


def test_duplicate_account_is_refused(repo: AccountRepository) -> None:
    """One account per user; the connection stays usable afterwards."""
    user_id = str(uuid.uuid4())
    repo.create(user_id, "Test")

    with pytest.raises(AccountExistsError):
        repo.create(user_id, "Other")

    assert repo.get(user_id).display_name == "Test"


def test_unknown_user_has_no_account(repo: AccountRepository) -> None:
    """get, set_role and the updates return None for a user without account."""
    user_id = str(uuid.uuid4())

    assert repo.get(user_id) is None
    assert repo.set_role(user_id, AccountRole.ADMIN) is None
    assert repo.update_display_name(user_id, "Test") is None
    assert repo.complete_onboarding(user_id) is None


def test_updates(repo: AccountRepository) -> None:
    """Role and display name changes are saved."""
    user_id = str(uuid.uuid4())
    repo.create(user_id, "Test")

    repo.set_role(user_id, AccountRole.ADMIN)
    repo.update_display_name(user_id, "Renamed")

    account = repo.get(user_id)
    assert (account.role, account.display_name) == (AccountRole.ADMIN, "Renamed")


def test_onboarding_keeps_the_first_date(repo: AccountRepository) -> None:
    """Completing the onboarding twice keeps the first date."""
    user_id = str(uuid.uuid4())
    repo.create(user_id, "Test")

    first = repo.complete_onboarding(user_id).onboarding_completed_at
    second = repo.complete_onboarding(user_id).onboarding_completed_at

    assert first is not None
    assert second == first


def test_last_seen_is_written_at_most_once_a_minute(
    conn, repo: AccountRepository
) -> None:
    """A recent last_seen_at is kept; an older one is refreshed."""
    user_id = str(uuid.uuid4())
    repo.create(user_id, "Test")

    repo.touch_last_seen(user_id)
    first = repo.get(user_id).last_seen_at
    repo.touch_last_seen(user_id)
    assert repo.get(user_id).last_seen_at == first

    _set_last_seen(conn, user_id, "2 minutes")
    old = repo.get(user_id).last_seen_at
    repo.touch_last_seen(user_id)
    assert repo.get(user_id).last_seen_at > old


def test_list_accounts_filters_by_status(repo: AccountRepository) -> None:
    """list_accounts returns every account, or only one status."""
    pending, approved = str(uuid.uuid4()), str(uuid.uuid4())
    repo.create(pending, "Pending")
    repo.create(approved, "Approved", status=AccountStatus.APPROVED)

    assert {a.user_id for a in repo.list_accounts()} == {pending, approved}
    assert [a.user_id for a in repo.list_accounts(AccountStatus.PENDING)] == [pending]


@pytest.mark.parametrize(
    "column_values",
    [
        ("display_name", "   "),
        ("status", "deleted"),
        ("role", "superadmin"),
    ],
)
def test_table_constraints(conn, column_values: tuple[str, str]) -> None:
    """The CHECK constraints of the migration refuse invalid values."""
    column, value = column_values
    values = {"display_name": "Test", "status": "pending", "role": "user"}
    values[column] = value

    with pytest.raises(psycopg2.errors.CheckViolation), conn.cursor() as cur:
        cur.execute(
            f'INSERT INTO "{_SCHEMA}"."{_TABLE}" '  # noqa: S608 — test table name
            "(user_id, display_name, status, role) VALUES (%s, %s, %s, %s)",
            (
                str(uuid.uuid4()),
                values["display_name"],
                values["status"],
                values["role"],
            ),
        )
    conn.rollback()
