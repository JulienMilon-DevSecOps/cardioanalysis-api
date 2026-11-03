"""Integration tests for the auth → user_profiles flow, against a real Postgres.

Activated automatically when ``DB_HOST_TEST`` is present in the environment
(same convention as cardiolab, see ``tests/database/test_repository.py`` there).
Uses an isolated ``_cardioanalysis_test_user_profiles`` table, created before
and dropped after each test — the real ``user_profiles`` table is never
touched. User ids are random test UUIDs, unrelated to any real account.
"""

import os
import time
import uuid

import jwt
import psycopg2
import pytest
from cardiolab.database.repository import HRVRepository
from fastapi import status
from fastapi.testclient import TestClient

from app.auth import dependency as auth_dependency
from app.routers import auth as auth_router

_TEST_TABLE = "_cardioanalysis_test_user_profiles"
_TEST_SECRET = "integration-test-secret-32-characters+"  # noqa: S105

_integration_skipif = pytest.mark.skipif(
    not os.getenv("DB_HOST_TEST"),
    reason=(
        "Set DB_HOST_TEST (+ DB_NAME_TEST, DB_USER_TEST, DB_PASSWORD_TEST) "
        "to run integration tests"
    ),
)


def _repo_from_test_env(**kwargs) -> HRVRepository:
    """Build an HRVRepository from DB_*_TEST environment variables.

    This keeps the test database separate from the production database.
    Required env vars: DB_HOST_TEST, DB_NAME_TEST, DB_USER_TEST, DB_PASSWORD_TEST.
    Optional: DB_PORT_TEST (defaults to 5432).
    """
    return HRVRepository(
        host=os.environ["DB_HOST_TEST"],
        database=os.environ["DB_NAME_TEST"],
        user=os.environ["DB_USER_TEST"],
        password=os.environ["DB_PASSWORD_TEST"],
        port=int(os.environ.get("DB_PORT_TEST", "5432")),
        **kwargs,
    )


def _drop_test_table() -> None:
    """Drop the isolated test table unconditionally (no-op if absent)."""
    conn = psycopg2.connect(
        host=os.environ["DB_HOST_TEST"],
        dbname=os.environ["DB_NAME_TEST"],
        user=os.environ["DB_USER_TEST"],
        password=os.environ["DB_PASSWORD_TEST"],
        port=int(os.environ.get("DB_PORT_TEST", "5432")),
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {_TEST_TABLE};")  # noqa: S608
        conn.commit()
    finally:
        conn.close()


def _make_token(sub: str) -> str:
    """Build a JWT signed with the test secret, mirroring Supabase's claims."""
    payload = {
        "sub": sub,
        "aud": "authenticated",
        "role": "authenticated",
        "email": f"{sub}@example.com",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, _TEST_SECRET, algorithm="HS256")


@pytest.mark.integration
@_integration_skipif
class TestMeWithRealDatabase:
    """Integration tests for GET /me against a real, isolated Postgres table."""

    @pytest.fixture(autouse=True)
    def _setup_and_teardown(self, monkeypatch: pytest.MonkeyPatch):
        """Create the isolated test table before, drop it after each test."""
        _drop_test_table()
        with _repo_from_test_env(user_profiles_table_name=_TEST_TABLE) as repo:
            repo.create_user_profiles_table()

        monkeypatch.setattr(auth_dependency, "SUPABASE_JWT_SECRET", _TEST_SECRET)
        monkeypatch.setattr(
            auth_router,
            "get_repository",
            lambda: _repo_from_test_env(user_profiles_table_name=_TEST_TABLE),
        )
        yield
        _drop_test_table()

    def test_me_returns_profile_saved_in_the_real_database(
        self, client: TestClient
    ) -> None:
        """A profile actually saved via HRVRepository is returned by /me."""
        user_id = str(uuid.uuid4())
        with _repo_from_test_env(user_profiles_table_name=_TEST_TABLE) as repo:
            repo.save_user_profile(
                user_id=user_id,
                primary_protocol="resting",
                sex="F",
                birth_year=1998,
                height_cm=170,
                hr_max=190,
                hr_rest=60,
                weight_kg=62,
            )

        token = _make_token(sub=user_id)
        response = client.get("/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["id"] == user_id
        assert body["profile"]["sex"] == "F"
        assert body["profile"]["birth_year"] == 1998

    def test_me_returns_null_profile_when_no_row_exists(
        self, client: TestClient
    ) -> None:
        """A user with no row in the (empty) test table gets profile: null."""
        user_id = str(uuid.uuid4())
        token = _make_token(sub=user_id)
        response = client.get("/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["profile"] is None
