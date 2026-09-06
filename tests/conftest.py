"""Shared fixtures for the test suite."""

from pathlib import Path

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
