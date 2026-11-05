"""Integration tests of ``AccountRepository`` against a real Postgres.

Activated when ``DB_HOST_TEST`` is set. The real ``accounts`` table is never
touched: isolated ``_cardioanalysis_test_accounts`` and
``_cardioanalysis_test_audit_log`` tables are built from the real migrations
(``A001__accounts.sql``, ``A002__audit_log.sql``), dropped before and after
each test.
"""

import os
import uuid

import psycopg2
import pytest

from app.services.accounts import (
    AccountAction,
    AccountExistsError,
    AccountNotFoundError,
    AccountRepository,
    AccountRole,
    AccountStatus,
    InvalidTransitionError,
    OwnAccountError,
)
from app.services.audit import AuditRepository
from app.services.migrations import MIGRATIONS_DIR

_SCHEMA = os.getenv("DB_SCHEMA_TEST", "cardioanalysis")
_TABLE = "_cardioanalysis_test_accounts"
_AUDIT = "_cardioanalysis_test_audit_log"

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
        for table in (_TABLE, _AUDIT):
            cur.execute(f'DROP TABLE IF EXISTS "{_SCHEMA}"."{table}"')
    conn.commit()


@pytest.fixture
def conn():
    """Open a connection with the isolated tables built from the real migrations."""
    connection = _connect()
    _drop(connection)
    accounts = (MIGRATIONS_DIR / "A001__accounts.sql").read_text(encoding="utf-8")
    journal = (MIGRATIONS_DIR / "A002__audit_log.sql").read_text(encoding="utf-8")
    with connection.cursor() as cur:
        cur.execute(f'SET search_path TO "{_SCHEMA}"')
        cur.execute(accounts.replace("accounts", _TABLE))
        cur.execute(journal.replace("audit_log", _AUDIT))
    connection.commit()
    yield connection
    connection.rollback()
    _drop(connection)
    connection.close()


@pytest.fixture
def repo(conn) -> AccountRepository:
    """Repository bound to the isolated tables."""
    return AccountRepository(conn, schema=_SCHEMA, table=_TABLE, audit_table=_AUDIT)


@pytest.fixture
def journal(conn) -> AuditRepository:
    """Journal reader bound to the isolated audit table."""
    return AuditRepository(conn, schema=_SCHEMA, name=_AUDIT)


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


# ── Status changes and journal (issue 3) ─────────────────────────────────────


def test_change_status_updates_and_journals_together(
    repo: AccountRepository, journal: AuditRepository
) -> None:
    """Status, author, date and the journal entry are written in one go."""
    admin, user = str(uuid.uuid4()), str(uuid.uuid4())
    repo.create(user, "Test")

    account = repo.change_status(user, AccountAction.APPROVE, actor_id=admin)

    assert account.status == AccountStatus.APPROVED
    assert account.status_changed_by == admin
    assert account.status_changed_at is not None
    entries, total = journal.list_entries()
    assert total == 1
    assert (entries[0].actor_id, entries[0].action, entries[0].target) == (
        admin,
        "account.approve",
        user,
    )
    assert entries[0].details == {"from": "pending", "to": "approved"}


def test_refused_change_leaves_no_trace(
    repo: AccountRepository, journal: AuditRepository
) -> None:
    """Invalid transition or own account: account unchanged, nothing journaled."""
    admin, user = str(uuid.uuid4()), str(uuid.uuid4())
    repo.create(user, "Test", status=AccountStatus.REJECTED)
    repo.create(admin, "Admin", status=AccountStatus.APPROVED)

    with pytest.raises(InvalidTransitionError):
        repo.change_status(user, AccountAction.APPROVE, actor_id=admin)
    with pytest.raises(OwnAccountError):
        repo.change_status(admin, AccountAction.SUSPEND, actor_id=admin)
    with pytest.raises(AccountNotFoundError):
        repo.change_status(str(uuid.uuid4()), AccountAction.APPROVE, actor_id=admin)

    assert repo.get(user).status == AccountStatus.REJECTED
    assert repo.get(admin).status == AccountStatus.APPROVED
    assert journal.list_entries() == ([], 0)


def test_full_cycle(repo: AccountRepository, journal: AuditRepository) -> None:
    """Approve, suspend, reactivate: three entries, newest first."""
    admin, user = str(uuid.uuid4()), str(uuid.uuid4())
    repo.create(user, "Test")

    for action in (
        AccountAction.APPROVE,
        AccountAction.SUSPEND,
        AccountAction.REACTIVATE,
    ):
        repo.change_status(user, action, actor_id=admin)

    entries, total = journal.list_entries()
    assert repo.get(user).status == AccountStatus.APPROVED
    assert total == 3
    assert [e.action for e in entries] == [
        "account.reactivate",
        "account.suspend",
        "account.approve",
    ]
    only_suspend, _ = journal.list_entries(action="account.suspend")
    assert [e.action for e in only_suspend] == ["account.suspend"]


def test_cli_writes_are_journaled(
    repo: AccountRepository, journal: AuditRepository
) -> None:
    """Journal create and set_role made with audit_source, without actor."""
    user = str(uuid.uuid4())

    repo.create(
        user,
        "Admin",
        status=AccountStatus.APPROVED,
        role=AccountRole.ADMIN,
        audit_source="cli",
    )
    repo.set_role(user, AccountRole.USER, audit_source="cli")

    entries, _ = journal.list_entries()
    assert [(e.action, e.actor_id) for e in entries] == [
        ("account.set_role", None),
        ("account.create", None),
    ]
    assert entries[0].details == {"source": "cli", "from": "admin", "to": "user"}
    assert entries[1].details == {
        "source": "cli",
        "status": "approved",
        "role": "admin",
    }


def test_writes_without_audit_source_are_not_journaled(
    repo: AccountRepository, journal: AuditRepository
) -> None:
    """Without audit_source (API-internal use), no journal entry."""
    user = str(uuid.uuid4())

    repo.create(user, "Test")
    repo.set_role(user, AccountRole.ADMIN)

    assert journal.list_entries() == ([], 0)


def test_search(repo: AccountRepository) -> None:
    """Name anywhere (case insensitive), id prefix, status, pagination, total."""
    nana = str(uuid.uuid4())
    repo.create(nana, "Nana")
    others = [str(uuid.uuid4()) for _ in range(3)]
    for i, user in enumerate(others):
        repo.create(user, f"Other {i}", status=AccountStatus.APPROVED)

    by_name, total_name = repo.search(q="NAN")
    by_id, _ = repo.search(q=others[0][:8])
    approved_page, total_approved = repo.search(
        status=AccountStatus.APPROVED, limit=2, offset=1
    )

    assert ([a.user_id for a in by_name], total_name) == ([nana], 1)
    assert others[0] in [a.user_id for a in by_id]
    assert total_approved == 3
    assert [a.user_id for a in approved_page] == others[1:]


def test_search_wildcards_are_literal(repo: AccountRepository) -> None:
    """A % or _ typed in the search does not match everything."""
    repo.create(str(uuid.uuid4()), "Test")

    assert repo.search(q="%") == ([], 0)
    assert repo.search(q="_") == ([], 0)
