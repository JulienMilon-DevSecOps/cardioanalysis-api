"""Current user endpoints: identity, account and HRV profile."""

from typing import Annotated

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependency import get_current_user
from app.auth.schemas import CurrentUser
from app.dependencies import get_account, get_accounts, require_status
from app.schemas.auth import (
    AccountResponse,
    MeResponse,
    UpdateMeRequest,
    UserProfileResponse,
)
from app.services.accounts import Account, AccountRepository, AccountStatus
from app.services.db import get_repository

router = APIRouter(tags=["auth"])

# Statuses whose /me shows the HRV profile; rejected and suspended see their
# status only
_PROFILE_STATUSES = {AccountStatus.PENDING, AccountStatus.APPROVED}

ApprovedAccount = Annotated[Account, Depends(require_status(AccountStatus.APPROVED))]


def _load_profile(user_id: str) -> UserProfileResponse | None:
    """Read the HRV profile through cardiolab; ``None`` if the user has none."""
    try:
        with get_repository() as repo:
            profile_data = repo.load_user_profile(user_id)
    except psycopg2.OperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from exc
    return UserProfileResponse(**profile_data) if profile_data else None


def _me_response(user: CurrentUser, account: Account) -> MeResponse:
    profile = _load_profile(user.id) if account.status in _PROFILE_STATUSES else None
    return MeResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        account=AccountResponse(
            status=account.status,
            role=account.role,
            display_name=account.display_name,
            onboarding_completed=account.onboarding_completed,
        ),
        profile=profile,
    )


@router.get("/me")
def read_current_user(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    account: Annotated[Account, Depends(get_account)],
) -> MeResponse:
    """Return the authenticated user, their account and HRV profile.

    Available whatever the account status, so that the front can show the
    right page (waiting for approval, rejected, suspended). A missing profile
    is not an error: ``profile`` is ``null``.
    """
    return _me_response(user, account)


@router.patch("/me")
def update_current_user(
    body: UpdateMeRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    account: ApprovedAccount,
    accounts: Annotated[AccountRepository, Depends(get_accounts)],
) -> MeResponse:
    """Change the display name (approved accounts).

    Email and password are changed through Supabase Auth, not here.
    """
    updated = accounts.update_display_name(account.user_id, body.display_name)
    return _me_response(user, updated or account)


@router.post("/me/onboarding/complete", status_code=status.HTTP_204_NO_CONTENT)
def complete_onboarding(
    account: ApprovedAccount,
    accounts: Annotated[AccountRepository, Depends(get_accounts)],
) -> None:
    """Record that the welcome screens were seen, for every device of the user."""
    accounts.complete_onboarding(account.user_id)
