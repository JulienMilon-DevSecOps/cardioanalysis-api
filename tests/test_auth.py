"""Unit tests for the authentication layer (JWT verification + GET /me).

Pure unit tests: JWTs are self-signed with a fixed test secret (no real
Supabase instance involved), and the database lookup is replaced by an
in-memory fake — no PostgreSQL connection needed to run these. User ids are
random test UUIDs, unrelated to any real account.
"""

import time
import uuid

import jwt
import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.auth import dependency as auth_dependency
from app.routers import auth as auth_router

_TEST_SECRET = "unit-test-secret-not-a-real-key-32chars+"  # noqa: S105
_USER_WITH_PROFILE_ID = str(uuid.uuid4())
_USER_WITHOUT_PROFILE_ID = str(uuid.uuid4())


def _make_token(sub: str = _USER_WITHOUT_PROFILE_ID, **overrides) -> str:
    """Build a JWT signed with the test secret, mirroring Supabase's claims."""
    payload = {
        "sub": sub,
        "aud": "authenticated",
        "role": "authenticated",
        "email": f"{sub}@example.com",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    payload.update(overrides)
    return jwt.encode(payload, _TEST_SECRET, algorithm="HS256")


@pytest.fixture(autouse=True)
def _patch_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify tokens against a fixed test secret, independent of any real .env."""
    monkeypatch.setattr(auth_dependency, "SUPABASE_JWT_SECRET", _TEST_SECRET)


class _FakeRepository:
    """Minimal stand-in for HRVRepository — no real database involved."""

    def __init__(self, profile: dict | None) -> None:
        """Store the canned profile this fake should return."""
        self._profile = profile

    def __enter__(self) -> "_FakeRepository":
        """Support the ``with get_repository() as repo:`` usage."""
        return self

    def __exit__(self, *exc_info: object) -> bool:
        """No-op exit — nothing to close on a fake connection."""
        return False

    def load_user_profile(self, user_id: str) -> dict | None:  # noqa: ARG002
        """Return the canned profile regardless of the requested user_id."""
        return self._profile


@pytest.fixture
def no_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make /me resolve a user with no row in user_profiles."""
    monkeypatch.setattr(auth_router, "get_repository", lambda: _FakeRepository(None))


@pytest.fixture
def with_profile(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Make /me resolve a user with an existing (fake) user_profiles row."""
    profile = {
        "primary_protocol": "orthostatic",
        "sex": "M",
        "birth_year": 1995,
        "height_cm": 192.0,
        "hr_max": None,
        "hr_rest": 55,
        "weight_kg": 78.0,
    }
    monkeypatch.setattr(auth_router, "get_repository", lambda: _FakeRepository(profile))
    return profile


class TestGetCurrentUser:
    """Unit tests for the JWT verification dependency, exercised via GET /me."""

    def test_missing_authorization_header(
        self, client: TestClient, no_profile: None
    ) -> None:
        """No Authorization header at all must return 401."""
        response = client.get("/me")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_header_without_bearer_prefix(
        self, client: TestClient, no_profile: None
    ) -> None:
        """A raw token without the 'Bearer ' prefix must return 401."""
        response = client.get("/me", headers={"Authorization": _make_token()})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_signature(self, client: TestClient, no_profile: None) -> None:
        """A token signed with the wrong secret must return 401."""
        bad_token = jwt.encode(
            {
                "sub": _USER_WITHOUT_PROFILE_ID,
                "aud": "authenticated",
                "exp": int(time.time()) + 3600,
            },
            "wrong-secret-also-32-characters+",
            algorithm="HS256",
        )
        response = client.get("/me", headers={"Authorization": f"Bearer {bad_token}"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_expired_token(self, client: TestClient, no_profile: None) -> None:
        """A token past its exp claim must return 401."""
        token = _make_token(exp=int(time.time()) - 10)
        response = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_wrong_audience_is_rejected(
        self, client: TestClient, no_profile: None
    ) -> None:
        """A token not meant for the 'authenticated' audience must return 401."""
        token = _make_token(aud="something-else")
        response = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_valid_token_without_profile(
        self, client: TestClient, no_profile: None
    ) -> None:
        """A valid token for a user with no profile row returns profile: null."""
        token = _make_token(sub=_USER_WITHOUT_PROFILE_ID)
        response = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["id"] == _USER_WITHOUT_PROFILE_ID
        assert body["profile"] is None

    def test_valid_token_with_profile(
        self, client: TestClient, with_profile: dict
    ) -> None:
        """A valid token for a user with a profile row returns it enriched."""
        token = _make_token(sub=_USER_WITH_PROFILE_ID)
        response = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["id"] == _USER_WITH_PROFILE_ID
        assert body["profile"] == with_profile
