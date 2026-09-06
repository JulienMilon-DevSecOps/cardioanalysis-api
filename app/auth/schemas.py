"""Pydantic schemas for the authenticated user."""

from pydantic import BaseModel


class CurrentUser(BaseModel):
    """Authenticated user, decoded from a verified Supabase JWT."""

    id: str
    email: str | None = None
    role: str
