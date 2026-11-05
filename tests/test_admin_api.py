"""Unit tests of the administration: transition rules, require_admin, /v1/admin.

Accounts and journal live in in-memory fakes (``tests/fakes.py``) that use the
real transition rules. User ids are random test UUIDs.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.auth import dependency as auth_dependency
from app.dependencies import get_accounts, get_audit_log
from app.main import app
from app.routers import auth as auth_router
from app.services.accounts import (
    AccountAction,
    AccountRole,
    AccountStatus,
    InvalidTransitionError,
    OwnAccountError,
    escape_like,
    next_status,
)
from tests.fakes import FakeAccountRepository, FakeAuditRepository, make_account

_TEST_SECRET = "unit-test-secret-not-a-real-key-32chars+"  # noqa: S105

S = AccountStatus


def _headers(sub: str) -> dict[str, str]:
    payload = {
        "sub": sub,
        "aud": "authenticated",
        "role": "authenticated",
        "exp": int(time.time()) + 3600,
    }
    return {"Authorization": f"Bearer {jwt.encode(payload, _TEST_SECRET, 'HS256')}"}


class TestTransitionRules:
    """next_status(): the four actions and the own-account guard, no HTTP."""

    @pytest.mark.parametrize(
        ("action", "current", "expected"),
        [
            (AccountAction.APPROVE, S.PENDING, S.APPROVED),
            (AccountAction.REJECT, S.PENDING, S.REJECTED),
            (AccountAction.SUSPEND, S.APPROVED, S.SUSPENDED),
            (AccountAction.REACTIVATE, S.SUSPENDED, S.APPROVED),
        ],
    )
    def test_allowed_transitions(
        self, action: AccountAction, current: AccountStatus, expected: AccountStatus
    ) -> None:
        """Each action leads from its only starting status to its target."""
        assert next_status(action, current, user_id="a", actor_id="b") == expected

    @pytest.mark.parametrize(
        ("action", "current"),
        [
            (action, current)
            for action in AccountAction
            for current in AccountStatus
            if (action, current)
            not in {
                (AccountAction.APPROVE, S.PENDING),
                (AccountAction.REJECT, S.PENDING),
                (AccountAction.SUSPEND, S.APPROVED),
                (AccountAction.REACTIVATE, S.SUSPENDED),
            }
        ],
    )
    def test_every_other_transition_is_refused(
        self, action: AccountAction, current: AccountStatus
    ) -> None:
        """The 12 other (action, status) pairs raise InvalidTransitionError."""
        with pytest.raises(InvalidTransitionError) as raised:
            next_status(action, current, user_id="a", actor_id="b")
        assert raised.value.current == current

    def test_own_account_is_refused_before_the_transition(self) -> None:
        """An administrator cannot act on their own account, whatever the action."""
        with pytest.raises(OwnAccountError):
            next_status(AccountAction.APPROVE, S.PENDING, user_id="a", actor_id="a")

    def test_escape_like(self) -> None:
        """Search wildcards typed by the admin are matched literally."""
        assert escape_like(r"50%_a\b") == r"50\%\_a\\b"


@pytest.fixture
def journal() -> FakeAuditRepository:
    """Shared in-memory journal."""
    return FakeAuditRepository()


@pytest.fixture
def store(journal: FakeAuditRepository):
    """Fake stores with an approved administrator; overrides the dependencies."""
    fake = FakeAccountRepository(journal=journal)
    app.dependency_overrides[get_accounts] = lambda: fake
    app.dependency_overrides[get_audit_log] = lambda: journal
    yield fake
    app.dependency_overrides.pop(get_accounts, None)
    app.dependency_overrides.pop(get_audit_log, None)


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_dependency, "SUPABASE_JWT_SECRET", _TEST_SECRET)


def _add(
    store: FakeAccountRepository,
    account_status: AccountStatus = S.PENDING,
    *,
    role: AccountRole = AccountRole.USER,
    name: str = "Test",
    days: int = 0,
) -> str:
    user_id = str(uuid.uuid4())
    store.accounts[user_id] = make_account(
        user_id,
        status=account_status,
        role=role,
        display_name=name,
        created_at=datetime(2026, 11, 1, tzinfo=UTC) + timedelta(days=days),
    )
    return user_id


@pytest.fixture
def admin(store: FakeAccountRepository) -> str:
    """Id of an approved administrator."""
    return _add(store, S.APPROVED, role=AccountRole.ADMIN, name="Admin", days=-1)


class TestRequireAdmin:
    """Every /v1/admin route is reserved to approved administrators."""

    @pytest.mark.parametrize(
        "path",
        [
            "/v1/admin/accounts",
            f"/v1/admin/accounts/{uuid.uuid4()}",
            "/v1/admin/audit",
        ],
    )
    def test_approved_user_gets_admin_only(
        self, client: TestClient, store: FakeAccountRepository, path: str
    ) -> None:
        """A plain user, even approved → 403 admin_only."""
        user = _add(store, S.APPROVED)

        response = client.get(path, headers=_headers(user))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["detail"]["code"] == "admin_only"

    def test_suspended_admin_loses_access(
        self, client: TestClient, store: FakeAccountRepository
    ) -> None:
        """An administrator whose account is suspended → 403 account_suspended."""
        suspended_admin = _add(store, S.SUSPENDED, role=AccountRole.ADMIN)

        response = client.get("/v1/admin/accounts", headers=_headers(suspended_admin))

        assert response.json()["detail"]["code"] == "account_suspended"

    def test_post_routes_are_protected_too(
        self, client: TestClient, store: FakeAccountRepository
    ) -> None:
        """A plain user cannot approve, even a pending account."""
        user = _add(store, S.APPROVED)
        target = _add(store, S.PENDING)

        response = client.post(
            f"/v1/admin/accounts/{target}/approve", headers=_headers(user)
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert store.get(target).status == S.PENDING


class TestListAccounts:
    """GET /v1/admin/accounts: filters, search, pagination."""

    def test_list_filters_by_status(
        self, client: TestClient, store: FakeAccountRepository, admin: str
    ) -> None:
        """status=pending returns only the pending accounts, with the total."""
        pending = _add(store, S.PENDING, days=1)
        _add(store, S.APPROVED, days=2)

        body = client.get(
            "/v1/admin/accounts?status=pending", headers=_headers(admin)
        ).json()

        assert [a["user_id"] for a in body["items"]] == [pending]
        assert body["total"] == 1

    def test_search_by_name_and_id_prefix(
        self, client: TestClient, store: FakeAccountRepository, admin: str
    ) -> None:
        """Search on the display name or on the start of the user id."""
        nana = _add(store, name="Nana", days=1)
        other = _add(store, name="Other", days=2)

        by_name = client.get("/v1/admin/accounts?q=nan", headers=_headers(admin))
        by_id = client.get(f"/v1/admin/accounts?q={other[:8]}", headers=_headers(admin))

        assert [a["user_id"] for a in by_name.json()["items"]] == [nana]
        assert other in [a["user_id"] for a in by_id.json()["items"]]

    def test_pagination(
        self, client: TestClient, store: FakeAccountRepository, admin: str
    ) -> None:
        """limit/offset return one page; total counts every match."""
        ids = [_add(store, days=i + 1) for i in range(3)]

        body = client.get(
            "/v1/admin/accounts?status=pending&limit=2&offset=1",
            headers=_headers(admin),
        ).json()

        assert [a["user_id"] for a in body["items"]] == ids[1:]
        assert (body["total"], body["limit"], body["offset"]) == (3, 2, 1)

    @pytest.mark.parametrize(
        "query", ["limit=0", "limit=201", "offset=-1", "status=deleted"]
    )
    def test_invalid_parameters(
        self, client: TestClient, store: FakeAccountRepository, admin: str, query: str
    ) -> None:
        """Out-of-range pagination or unknown status → 422."""
        response = client.get(f"/v1/admin/accounts?{query}", headers=_headers(admin))

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_no_health_data_in_the_response(
        self, client: TestClient, store: FakeAccountRepository, admin: str
    ) -> None:
        """The administration list holds account fields only."""
        _add(store, days=1)

        item = client.get("/v1/admin/accounts", headers=_headers(admin)).json()[
            "items"
        ][0]

        assert set(item) == {
            "user_id",
            "display_name",
            "status",
            "role",
            "created_at",
            "status_changed_at",
            "last_seen_at",
        }


class TestReadAccount:
    """GET /v1/admin/accounts/{user_id}."""

    def test_detail(
        self, client: TestClient, store: FakeAccountRepository, admin: str
    ) -> None:
        """The detail adds who changed the status and the onboarding flag."""
        user = _add(store, name="Nana")

        body = client.get(f"/v1/admin/accounts/{user}", headers=_headers(admin)).json()

        assert body["display_name"] == "Nana"
        assert body["onboarding_completed"] is False
        assert body["status_changed_by"] is None

    def test_unknown_account(
        self, client: TestClient, store: FakeAccountRepository, admin: str
    ) -> None:
        """No account → 404 account_not_found."""
        response = client.get(
            f"/v1/admin/accounts/{uuid.uuid4()}", headers=_headers(admin)
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"]["code"] == "account_not_found"

    def test_id_must_be_a_uuid(
        self, client: TestClient, store: FakeAccountRepository, admin: str
    ) -> None:
        """A malformed id in the path → 422, before any database access."""
        response = client.get("/v1/admin/accounts/juju", headers=_headers(admin))

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestStatusActions:
    """POST /v1/admin/accounts/{user_id}/approve|reject|suspend|reactivate."""

    @pytest.mark.parametrize(
        ("action", "current", "expected"),
        [
            ("approve", S.PENDING, S.APPROVED),
            ("reject", S.PENDING, S.REJECTED),
            ("suspend", S.APPROVED, S.SUSPENDED),
            ("reactivate", S.SUSPENDED, S.APPROVED),
        ],
    )
    def test_action_changes_the_status_and_is_journaled(
        self,
        client: TestClient,
        store: FakeAccountRepository,
        journal: FakeAuditRepository,
        admin: str,
        action: str,
        current: AccountStatus,
        expected: AccountStatus,
    ) -> None:
        """The account changes, records who did it, and a journal entry is added."""
        user = _add(store, current)

        response = client.post(
            f"/v1/admin/accounts/{user}/{action}", headers=_headers(admin)
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == expected
        assert response.json()["status_changed_by"] == admin
        entry = journal.entries[-1]
        assert (entry.actor_id, entry.action, entry.target) == (
            admin,
            f"account.{action}",
            user,
        )
        assert entry.details == {"from": str(current), "to": str(expected)}

    def test_invalid_transition(
        self,
        client: TestClient,
        store: FakeAccountRepository,
        journal: FakeAuditRepository,
        admin: str,
    ) -> None:
        """Approving a rejected account → 409 with the current status, no entry."""
        user = _add(store, S.REJECTED)

        response = client.post(
            f"/v1/admin/accounts/{user}/approve", headers=_headers(admin)
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["detail"]["code"] == "invalid_transition"
        assert response.json()["detail"]["current_status"] == "rejected"
        assert journal.entries == []

    def test_admin_cannot_suspend_themselves(
        self, client: TestClient, store: FakeAccountRepository, admin: str
    ) -> None:
        """Own account → 409 own_account, the admin stays approved."""
        response = client.post(
            f"/v1/admin/accounts/{admin}/suspend", headers=_headers(admin)
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["detail"]["code"] == "own_account"
        assert store.get(admin).status == S.APPROVED

    def test_unknown_account(
        self, client: TestClient, store: FakeAccountRepository, admin: str
    ) -> None:
        """No account → 404 account_not_found."""
        response = client.post(
            f"/v1/admin/accounts/{uuid.uuid4()}/approve", headers=_headers(admin)
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_suspension_applies_to_the_next_request(
        self,
        client: TestClient,
        store: FakeAccountRepository,
        admin: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A suspended user is refused at once, with the same token."""
        monkeypatch.setattr(auth_router, "_load_profile", lambda user_id: None)
        user = _add(store, S.APPROVED)
        headers = _headers(user)
        before = client.patch("/v1/me", headers=headers, json={"display_name": "A"})

        client.post(f"/v1/admin/accounts/{user}/suspend", headers=_headers(admin))
        after = client.patch("/v1/me", headers=headers, json={"display_name": "B"})

        assert before.status_code == status.HTTP_200_OK
        assert after.status_code == status.HTTP_403_FORBIDDEN
        assert after.json()["detail"]["code"] == "account_suspended"


class TestBulkAction:
    """POST /v1/admin/accounts/bulk: each account processed independently."""

    def test_mixed_results(
        self, client: TestClient, store: FakeAccountRepository, admin: str
    ) -> None:
        """Approved, invalid, missing and own account in one call, in order."""
        pending = _add(store, S.PENDING)
        approved = _add(store, S.APPROVED)
        missing = str(uuid.uuid4())

        response = client.post(
            "/v1/admin/accounts/bulk",
            headers=_headers(admin),
            json={"ids": [pending, approved, missing, admin], "action": "approve"},
        )

        assert response.status_code == status.HTTP_200_OK
        assert [
            (r["user_id"], r["ok"], r["status"], r["code"])
            for r in response.json()["results"]
        ] == [
            (pending, True, "approved", None),
            (approved, False, "approved", "invalid_transition"),
            (missing, False, None, "account_not_found"),
            (admin, False, None, "own_account"),
        ]
        assert store.get(pending).status == S.APPROVED

    def test_duplicate_ids_are_processed_once(
        self, client: TestClient, store: FakeAccountRepository, admin: str
    ) -> None:
        """The same id twice gives one result, not a second invalid transition."""
        pending = _add(store, S.PENDING)

        results = client.post(
            "/v1/admin/accounts/bulk",
            headers=_headers(admin),
            json={"ids": [pending, pending], "action": "approve"},
        ).json()["results"]

        assert len(results) == 1

    @pytest.mark.parametrize(
        "body",
        [
            {"ids": [], "action": "approve"},
            {"ids": ["not-a-uuid"], "action": "approve"},
            {"ids": [str(uuid.uuid4())], "action": "delete"},
            {"ids": [str(uuid.uuid4()) for _ in range(101)], "action": "approve"},
        ],
    )
    def test_invalid_body(
        self, client: TestClient, store: FakeAccountRepository, admin: str, body: dict
    ) -> None:
        """Empty list, bad id, unknown action or more than 100 ids → 422."""
        response = client.post(
            "/v1/admin/accounts/bulk", headers=_headers(admin), json=body
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestAuditJournal:
    """GET /v1/admin/audit."""

    def test_journal_lists_actions_newest_first(
        self, client: TestClient, store: FakeAccountRepository, admin: str
    ) -> None:
        """Two actions → two entries, the last one first; action filter."""
        user = _add(store, S.PENDING)
        client.post(f"/v1/admin/accounts/{user}/approve", headers=_headers(admin))
        client.post(f"/v1/admin/accounts/{user}/suspend", headers=_headers(admin))

        body = client.get("/v1/admin/audit", headers=_headers(admin)).json()
        filtered = client.get(
            "/v1/admin/audit?action=account.approve", headers=_headers(admin)
        ).json()

        assert [e["action"] for e in body["items"]] == [
            "account.suspend",
            "account.approve",
        ]
        assert body["total"] == 2
        assert [e["action"] for e in filtered["items"]] == ["account.approve"]
