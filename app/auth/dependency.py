"""FastAPI dependency verifying Supabase-issued JWTs.

Verification is purely cryptographic (HS256 signature check against
``SUPABASE_JWT_SECRET``) — no network call to Supabase Auth on every request.
"""

import jwt
from fastapi import Header, HTTPException, status

from app.auth.schemas import CurrentUser
from app.config import SUPABASE_JWT_SECRET

# GoTrue issues user access tokens with this fixed audience (GOTRUE_JWT_AUD).
# The static anon/service_role API keys carry no "aud" claim at all, so this
# also rejects them if used by mistake as a bearer token.
_EXPECTED_AUDIENCE = "authenticated"


async def get_current_user(
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    """Resolve the authenticated user from the ``Authorization`` header.

    Args:
        authorization: Raw ``Authorization`` header, expected as
            ``Bearer <token>``.

    Returns:
        The user decoded from the token's claims.

    Raises:
        HTTPException: 401 if the header is missing, malformed, or the token
            is invalid, expired, or not meant for this audience.

    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
        )
    token = authorization.removeprefix("Bearer ").strip()

    try:
        claims = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience=_EXPECTED_AUDIENCE,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc

    return CurrentUser(
        id=claims["sub"],
        email=claims.get("email"),
        role=claims.get("role", "authenticated"),
    )
