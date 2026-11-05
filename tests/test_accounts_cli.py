"""Unit tests of ``python -m app.accounts`` against an in-memory account store."""

import uuid

import pytest

from app import accounts as cli
from app.services.accounts import AccountRole, AccountStatus
from tests.fakes import FakeAccountRepository, make_account


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeAccountRepository:
    """Run the command against a fake connection and an in-memory store."""
    fake = FakeAccountRepository()
    monkeypatch.setattr(cli, "get_connection", _FakeConnection)
    monkeypatch.setattr(cli, "AccountRepository", lambda conn: fake)
    return fake


def test_create_first_admin(
    store: FakeAccountRepository, capsys: pytest.CaptureFixture[str]
) -> None:
    """Create the first administrator (approved, admin)."""
    user_id = str(uuid.uuid4())

    code = cli.main(
        [
            "create",
            user_id,
            "--display-name",
            " Admin ",
            "--status",
            "approved",
            "--role",
            "admin",
        ]
    )

    assert code == 0
    account = store.get(user_id)
    assert (account.display_name, account.status, account.role) == (
        "Admin",
        AccountStatus.APPROVED,
        AccountRole.ADMIN,
    )
    assert "Created:" in capsys.readouterr().out


def test_create_defaults_to_pending_user(store: FakeAccountRepository) -> None:
    """Without options, the account waits for approval, as a plain user."""
    user_id = str(uuid.uuid4())

    cli.main(["create", user_id, "--display-name", "Test"])

    assert store.get(user_id).status == AccountStatus.PENDING
    assert store.get(user_id).role == AccountRole.USER


def test_create_twice_fails(
    store: FakeAccountRepository, capsys: pytest.CaptureFixture[str]
) -> None:
    """A second account for the same user is refused with code 1."""
    user_id = str(uuid.uuid4())
    cli.main(["create", user_id, "--display-name", "Test"])

    assert cli.main(["create", user_id, "--display-name", "Other"]) == 1
    assert "already exists" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["   ", "x" * 51])
def test_create_with_invalid_name_fails(
    store: FakeAccountRepository, name: str
) -> None:
    """Empty or too long display name → code 1, nothing created."""
    user_id = str(uuid.uuid4())

    assert cli.main(["create", user_id, "--display-name", name]) == 1
    assert store.get(user_id) is None


def test_user_id_must_be_a_uuid(store: FakeAccountRepository) -> None:
    """A malformed user id is refused by the argument parser."""
    with pytest.raises(SystemExit):
        cli.main(["create", "juju", "--display-name", "Test"])


def test_set_role(store: FakeAccountRepository) -> None:
    """set-role changes the role of an existing account."""
    user_id = str(uuid.uuid4())
    store.accounts[user_id] = make_account(user_id)

    assert cli.main(["set-role", user_id, "admin"]) == 0
    assert store.get(user_id).role == AccountRole.ADMIN


def test_set_role_of_unknown_user_fails(
    store: FakeAccountRepository, capsys: pytest.CaptureFixture[str]
) -> None:
    """set-role on a user without account → code 1."""
    assert cli.main(["set-role", str(uuid.uuid4()), "admin"]) == 1
    assert "No account" in capsys.readouterr().err


def test_list_filters_by_status(
    store: FakeAccountRepository, capsys: pytest.CaptureFixture[str]
) -> None:
    """Show only the accounts of the requested status."""
    pending, approved = str(uuid.uuid4()), str(uuid.uuid4())
    store.accounts[pending] = make_account(pending, status=AccountStatus.PENDING)
    store.accounts[approved] = make_account(approved)

    assert cli.main(["list", "--status", "pending"]) == 0

    out = capsys.readouterr().out
    assert pending in out
    assert approved not in out
    assert "last seen never" in out


def test_database_error_exits_with_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A database error (e.g. migrations not applied) ends with code 1."""

    class _Broken:
        def __init__(self, conn) -> None:
            pass

        def list_accounts(self, status=None):
            raise RuntimeError('relation "accounts" does not exist')

    conn = _FakeConnection()
    monkeypatch.setattr(cli, "get_connection", lambda: conn)
    monkeypatch.setattr(cli, "AccountRepository", _Broken)

    assert cli.main(["list"]) == 1
    assert "does not exist" in capsys.readouterr().err
    assert conn.closed


def test_connection_failure_exits_with_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unreachable database ends with code 1."""

    def refuse():
        raise OSError("connection refused")

    monkeypatch.setattr(cli, "get_connection", refuse)

    assert cli.main(["list"]) == 1
    assert "Cannot connect" in capsys.readouterr().err


def test_create_and_set_role_are_journaled_as_cli(store: FakeAccountRepository) -> None:
    """Both writes ask the service for a journal entry with source "cli"."""
    user_id = str(uuid.uuid4())

    cli.main(["create", user_id, "--display-name", "Test"])
    cli.main(["set-role", user_id, "admin"])

    assert store.audit_sources == ["cli", "cli"]
