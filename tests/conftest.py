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
#
# Only "db" and "supavisor" (the pooler) are started — that's all these
# integration tests actually touch (direct SQL via psycopg2/HRVRepository).
# Deliberately NOT the full stack: "studio" (Next.js) is memory-heavy and was
# seen crashing under load on this machine, and nothing here needs the HTTP
# gateway/auth API. Neither "db" nor "supavisor" depends on "studio", so this
# also sidesteps the api-gw → studio dependency chain entirely.

# Stack location. Default: the sibling private repository
# cardioanalysis-supabase-local-nas (same defaults as cardiolab's conftest).
# Overridable in .env for a stack deployed elsewhere (e.g. example/auth copied
# into local/auth): SUPABASE_DIR is relative to the project root (or absolute),
# SUPABASE_ENV_FILE relative to SUPABASE_DIR.
_SUPABASE_DIR = (
    Path(__file__).resolve().parent.parent
    / Path(
        os.getenv("SUPABASE_DIR", "../cardioanalysis-supabase-local-nas")
    ).expanduser()
).resolve()
_COMPOSE_CMD = [
    "docker",
    "compose",
    "--env-file",
    os.getenv("SUPABASE_ENV_FILE", "local/.env"),
]
_MINIMAL_SERVICES = {"db": "supabase-db", "supavisor": "supabase-pooler"}
# Services that this session started (and stops again at the end): a service
# already running before pytest (e.g. db started by hand) is left as it was.
_services_we_started: list[str] = []


def _running_services() -> set[str]:
    """Return the minimal services whose container is currently running."""
    running: set[str] = set()
    for service, container in _MINIMAL_SERVICES.items():
        try:
            result = subprocess.run(  # noqa: S603 — fixed container names
                ["docker", "inspect", "-f", "{{.State.Running}}", container],  # noqa: S607
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return set()
        if result.returncode == 0 and result.stdout.strip() == "true":
            running.add(service)
    return running


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
    # En CI, la base est fournie par le service postgres de la pipeline
    if os.getenv("CI"):
        return
    has_integration_tests = any(
        item.get_closest_marker("integration") for item in session.items
    )
    if not has_integration_tests:
        return
    # Both are needed: the tests connect through the pooler. A db started alone
    # (e.g. by hand) without supavisor would make every integration test fail.
    missing = [s for s in _MINIMAL_SERVICES if s not in _running_services()]
    if not missing:
        return
    try:
        subprocess.run(  # noqa: S603
            [
                *_COMPOSE_CMD,
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "120",
                *missing,
            ],
            cwd=_SUPABASE_DIR,
            check=False,
        )
    except FileNotFoundError:
        return  # Docker not available — the tests' own skipif/connection error will explain why.
    _services_we_started.extend(missing)
    _wait_until_postgres_accepts_connections()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:  # noqa: ARG001
    """Stop the services of the local Supabase stack that this session started."""
    if _services_we_started:
        subprocess.run(  # noqa: S603
            [*_COMPOSE_CMD, "stop", *_services_we_started],
            cwd=_SUPABASE_DIR,
            check=False,
        )
