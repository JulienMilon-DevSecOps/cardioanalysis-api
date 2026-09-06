"""Auth endpoints response schemas."""

from pydantic import BaseModel


class UserProfileResponse(BaseModel):
    """HRV-relevant profile fields, from cardiolab's ``user_profiles`` table."""

    primary_protocol: str
    sex: str | None = None
    birth_year: int | None = None
    height_cm: float | None = None
    hr_max: int | None = None
    hr_rest: int | None = None
    weight_kg: float | None = None


class MeResponse(BaseModel):
    """Response for ``GET /me`` — identity plus HRV profile, if any."""

    id: str
    email: str | None = None
    role: str
    profile: UserProfileResponse | None = None
