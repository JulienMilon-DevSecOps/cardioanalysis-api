"""Shared fixtures for the test suite."""

import os
import subprocess
import time
from pathlib import Path

import psycopg2
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from app.main import app

# Explicit load (not relying on app.config's own load_dotenv running as an
# import side effect) so DB_*_TEST is available even to test modules that
# never import anything under app.* (e.g. a repository-only integration test).
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


@pytest.fixture
def client() -> TestClient:
    """Return a TestClient bound to the FastAPI app."""
    with TestClient(app) as c:
        yield c


# ── Auto start/stop the local Supabase stack for integration tests ─────────────
# Only touches Docker when at least one collected test needs it, and only
# stops it again if this very session is the one that started it — keeps the
# stack off (no CPU/RAM usage) the rest of the time. Uses `stop`/`start`
# (not `down`/`up`) so restarts are fast, no re-init of Postgres.

_SUPABASE_DIR = Path(__file__).resolve().parent.parent / "local" / "auth"
_COMPOSE_CMD = ["docker", "compose", "--env-file", "dev/.env"]
_we_started_supabase = False


def _supabase_is_running() -> bool:
    """Check whether the local Supabase stack's db container is up."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", "supabase-db"],  # noqa: S603, S607
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _wait_until_postgres_accepts_connections(timeout: float = 30.0) -> None:
    """Retry a real connection — Docker's healthcheck can pass before Supavisor is.

    Seen in practice: `docker compose up --wait` reports healthy while
    Supavisor is still finishing its own internal migrations/reconnection,
    so the very next real query can fail with "server closed the connection
    unexpectedly". A few short retries absorb that window.
    """
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            conn = psycopg2.connect(
                host=os.environ["DB_HOST_TEST"],
                dbname=os.environ["DB_NAME_TEST"],
                user=os.environ["DB_USER_TEST"],
                password=os.environ["DB_PASSWORD_TEST"],
                port=int(os.environ.get("DB_PORT_TEST", "5432")),
                connect_timeout=3,
            )
        except (psycopg2.OperationalError, KeyError) as exc:
            last_error = exc
            time.sleep(1)
            continue
        conn.close()
        return
    if last_error is not None:
        # Not fatal here — let the actual test surface the real error/skip.
        pass


def pytest_collection_finish(session: pytest.Session) -> None:
    """Start the local Supabase stack if integration tests will actually run."""
    global _we_started_supabase  # noqa: PLW0603
    has_integration_tests = any(
        item.get_closest_marker("integration") for item in session.items
    )
    if not has_integration_tests or _supabase_is_running():
        return
    try:
        subprocess.run(  # noqa: S603
            [*_COMPOSE_CMD, "up", "-d", "--wait", "--wait-timeout", "120"],
            cwd=_SUPABASE_DIR,
            check=False,
        )
    except FileNotFoundError:
        return  # Docker not available — the tests' own skipif/connection error will explain why.
    _we_started_supabase = True
    _wait_until_postgres_accepts_connections()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:  # noqa: ARG001
    """Stop the local Supabase stack if this session is the one that started it."""
    if _we_started_supabase:
        subprocess.run([*_COMPOSE_CMD, "stop"], cwd=_SUPABASE_DIR, check=False)  # noqa: S603
