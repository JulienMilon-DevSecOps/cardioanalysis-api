"""Health check response schema."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Health check API response."""

    status: str
    version: str
