"""Auth endpoints request and response schemas."""

from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

DisplayName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)
]


class UserProfileResponse(BaseModel):
    """HRV-relevant profile fields, from cardiolab's ``user_profiles`` table."""

    primary_protocol: str
    sex: str | None = None
    birth_year: int | None = None
    height_cm: float | None = None
    hr_max: int | None = None
    hr_rest: int | None = None
    weight_kg: float | None = None


class AccountResponse(BaseModel):
    """Application account of the user: beta status, role, display name."""

    status: Literal["pending", "approved", "rejected", "suspended"]
    role: Literal["user", "admin"]
    display_name: str
    onboarding_completed: bool


class MeResponse(BaseModel):
    """Response for ``GET /v1/me`` — identity, account and HRV profile.

    ``profile`` is ``null`` when the user has none, and always for a rejected
    or suspended account (status only).
    """

    id: str
    email: str | None = None
    role: str
    account: AccountResponse
    profile: UserProfileResponse | None = None


class UpdateMeRequest(BaseModel):
    """Body of ``PATCH /v1/me``. Email and password are changed through Supabase Auth."""

    display_name: DisplayName
