"""Tests for GET /health."""

from fastapi import status
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    """Test suite for the health check endpoint."""

    def test_returns_200(self, client: TestClient) -> None:
        """GET /health must return HTTP 200."""
        response = client.get("/health")
        assert response.status_code == status.HTTP_200_OK

    def test_status_is_ok(self, client: TestClient) -> None:
        """Response body must contain status 'ok'."""
        response = client.get("/health")
        assert response.json()["status"] == "ok"

    def test_version_is_present(self, client: TestClient) -> None:
        """Response body must contain a non-empty version string."""
        response = client.get("/health")
        version = response.json()["version"]
        assert isinstance(version, str)
        assert len(version) > 0
