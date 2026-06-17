"""Health check endpoint."""

from fastapi import APIRouter, status

from app.config import APP_VERSION
from app.schemas.health import HealthResponse

router = APIRouter()


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> HealthResponse:
    """Return service status and current API version."""
    return HealthResponse(status="ok", version=APP_VERSION)
