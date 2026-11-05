"""Unit tests of the account layer: get_account, require_status and /v1/me routes.

Accounts live in an in-memory fake (``tests/fakes.py``); the HRV profile
lookup is stubbed. User ids are random test UUIDs.
"""

import time
import uuid
from typing import Annotated

import jwt
import psycopg2
import pytest
from fastapi import Depends, FastAPI, status
from fastapi.testclient import TestClient

from app import dependencies
from app.auth import dependency as auth_dependency
from app.dependencies import get_accounts, require_status
from app.main import app
from app.routers import auth as auth_router
from app.services.accounts import Account, AccountStatus
from tests.fakes import FakeAccountRepository, make_account

_TEST_SECRET = "unit-test-secret-not-a-real-key-32chars+"  # noqa: S105
_PROFILE = {"primary_protocol": "resting"}


def _headers(sub: str) -> dict[str, str]:
    payload = {
        "sub": sub,
        "aud": "authenticated",
        "role": "authenticated",
        "email": "user@example.com",
        "exp": int(time.time()) + 3600,
    }
    return {"Authorization": f"Bearer {jwt.encode(payload, _TEST_SECRET, 'HS256')}"}


class _ProfileRepository:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def load_user_profile(self, user_id: str) -> dict:  # noqa: ARG002
        return _PROFILE


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a fixed JWT secret and give every user an HRV profile."""
    monkeypatch.setattr(auth_dependency, "SUPABASE_JWT_SECRET", _TEST_SECRET)
    monkeypatch.setattr(auth_router, "get_repository", _ProfileRepository)


@pytest.fixture
def accounts():
    """Install an empty fake account store for the request."""
    fake = FakeAccountRepository()
    app.dependency_overrides[get_accounts] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_accounts, None)


def _user(accounts: FakeAccountRepository, account_status: AccountStatus) -> str:
    user_id = str(uuid.uuid4())
    accounts.accounts[user_id] = make_account(user_id, status=account_status)
    return user_id


class TestGetAccount:
    """Account resolution, whatever the route."""

    def test_user_without_account_gets_account_missing(
        self, client: TestClient, accounts: FakeAccountRepository
    ) -> None:
        """A valid token for a user with no account → 403 account_missing."""
        response = client.get("/v1/me", headers=_headers(str(uuid.uuid4())))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["detail"]["code"] == "account_missing"

    def test_subject_that_is_not_a_user_id_gets_account_missing(
        self, client: TestClient, accounts: FakeAccountRepository
    ) -> None:
        """A token whose subject is not a UUID never reaches the database."""
        response = client.get("/v1/me", headers=_headers("not-a-uuid"))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["detail"]["code"] == "account_missing"

    def test_activity_is_recorded(
        self, client: TestClient, accounts: FakeAccountRepository
    ) -> None:
        """Each authenticated request asks to update last_seen_at."""
        user_id = _user(accounts, AccountStatus.PENDING)

        client.get("/v1/me", headers=_headers(user_id))

        assert accounts.touched == [user_id]

    def test_database_down_returns_503(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No connection to the database → 503, not 500."""

        def refuse():
            raise psycopg2.OperationalError("connection refused")

        monkeypatch.setattr(dependencies, "get_connection", refuse)

        response = client.get("/v1/me", headers=_headers(str(uuid.uuid4())))

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


class TestReadMe:
    """GET /v1/me for every status."""

    @pytest.mark.parametrize(
        "account_status", [AccountStatus.PENDING, AccountStatus.APPROVED]
    )
    def test_pending_and_approved_see_their_profile(
        self,
        client: TestClient,
        accounts: FakeAccountRepository,
        account_status: AccountStatus,
    ) -> None:
        """Pending and approved accounts get the account and the profile."""
        user_id = _user(accounts, account_status)

        body = client.get("/v1/me", headers=_headers(user_id)).json()

        assert body["account"]["status"] == account_status
        assert body["profile"]["primary_protocol"] == "resting"

    @pytest.mark.parametrize(
        "account_status", [AccountStatus.REJECTED, AccountStatus.SUSPENDED]
    )
    def test_rejected_and_suspended_see_their_status_only(
        self,
        client: TestClient,
        accounts: FakeAccountRepository,
        account_status: AccountStatus,
    ) -> None:
        """Rejected and suspended accounts get 200 with no profile."""
        user_id = _user(accounts, account_status)

        response = client.get("/v1/me", headers=_headers(user_id))

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["account"]["status"] == account_status
        assert response.json()["profile"] is None


class TestUpdateMe:
    """PATCH /v1/me (display name)."""

    def test_approved_account_changes_its_display_name(
        self, client: TestClient, accounts: FakeAccountRepository
    ) -> None:
        """The name is stripped, saved and returned."""
        user_id = _user(accounts, AccountStatus.APPROVED)

        response = client.patch(
            "/v1/me", headers=_headers(user_id), json={"display_name": "  Nana "}
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["account"]["display_name"] == "Nana"
        assert accounts.get(user_id).display_name == "Nana"

    @pytest.mark.parametrize("name", ["", "   ", "x" * 51])
    def test_invalid_display_name_is_rejected(
        self, client: TestClient, accounts: FakeAccountRepository, name: str
    ) -> None:
        """Empty or longer than 50 characters → 422, nothing saved."""
        user_id = _user(accounts, AccountStatus.APPROVED)

        response = client.patch(
            "/v1/me", headers=_headers(user_id), json={"display_name": name}
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert accounts.get(user_id).display_name == "Test"

    def test_pending_account_cannot_change_its_name(
        self, client: TestClient, accounts: FakeAccountRepository
    ) -> None:
        """Only approved accounts → 403 account_pending."""
        user_id = _user(accounts, AccountStatus.PENDING)

        response = client.patch(
            "/v1/me", headers=_headers(user_id), json={"display_name": "Nana"}
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["detail"]["code"] == "account_pending"


class TestCompleteOnboarding:
    """POST /v1/me/onboarding/complete."""

    def test_completion_is_recorded_once(
        self, client: TestClient, accounts: FakeAccountRepository
    ) -> None:
        """204, the date is set, a second call keeps the first date."""
        user_id = _user(accounts, AccountStatus.APPROVED)

        first = client.post("/v1/me/onboarding/complete", headers=_headers(user_id))
        date = accounts.get(user_id).onboarding_completed_at
        client.post("/v1/me/onboarding/complete", headers=_headers(user_id))

        assert first.status_code == status.HTTP_204_NO_CONTENT
        assert date is not None
        assert accounts.get(user_id).onboarding_completed_at == date
        me = client.get("/v1/me", headers=_headers(user_id)).json()
        assert me["account"]["onboarding_completed"] is True

    def test_suspended_account_is_refused(
        self, client: TestClient, accounts: FakeAccountRepository
    ) -> None:
        """Only approved accounts → 403 account_suspended."""
        user_id = _user(accounts, AccountStatus.SUSPENDED)

        response = client.post("/v1/me/onboarding/complete", headers=_headers(user_id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["detail"]["code"] == "account_suspended"


class TestRequireStatus:
    """require_status() on its own, with every status."""

    @pytest.fixture
    def guarded_client(self, accounts: FakeAccountRepository) -> TestClient:
        """Build a small app whose only route accepts pending and approved accounts."""
        guarded = FastAPI()

        pending_or_approved = require_status(
            AccountStatus.PENDING, AccountStatus.APPROVED
        )

        @guarded.get("/guarded")
        def guarded_route(
            account: Annotated[Account, Depends(pending_or_approved)],
        ) -> dict:
            return {"status": account.status}

        guarded.dependency_overrides[get_accounts] = lambda: accounts
        return TestClient(guarded)

    @pytest.mark.parametrize(
        ("account_status", "expected_code", "expected_detail"),
        [
            (AccountStatus.PENDING, 200, None),
            (AccountStatus.APPROVED, 200, None),
            (AccountStatus.REJECTED, 403, "account_rejected"),
            (AccountStatus.SUSPENDED, 403, "account_suspended"),
        ],
    )
    def test_each_status(
        self,
        guarded_client: TestClient,
        accounts: FakeAccountRepository,
        account_status: AccountStatus,
        expected_code: int,
        expected_detail: str | None,
    ) -> None:
        """Allowed statuses pass; the others get 403 account_<status>."""
        user_id = _user(accounts, account_status)

        response = guarded_client.get("/guarded", headers=_headers(user_id))

        assert response.status_code == expected_code
        if expected_detail:
            assert response.json()["detail"]["code"] == expected_detail
            assert response.json()["detail"]["message"]
