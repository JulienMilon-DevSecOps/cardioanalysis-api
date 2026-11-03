"""Authentication endpoints — current user resolution."""

from typing import Annotated

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependency import get_current_user
from app.auth.schemas import CurrentUser
from app.schemas.auth import MeResponse, UserProfileResponse
from app.services.db import get_repository

router = APIRouter(tags=["auth"])


@router.get("/me")
async def read_current_user(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> MeResponse:
    """Return the authenticated user plus their HRV profile, if any.

    The profile lookup is a plain PostgreSQL query (via cardiolab's
    ``HRVRepository``) — a missing profile is not an error, ``profile`` is
    simply ``null`` in that case.
    """
    try:
        with get_repository() as repo:
            profile_data = repo.load_user_profile(user.id)
    except psycopg2.OperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from exc

    profile = UserProfileResponse(**profile_data) if profile_data else None
    return MeResponse(id=user.id, email=user.email, role=user.role, profile=profile)
