"""Shared fixtures for the test suite."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Return a TestClient bound to the FastAPI app."""
    with TestClient(app) as c:
        yield c
