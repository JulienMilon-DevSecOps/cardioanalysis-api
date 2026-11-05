"""FastAPI dependencies that combine authentication and the database.

``app/auth/`` only verifies the JWT (no SQL); this module chains it with the
account read from the database. Order of the chain::

    get_current_user  → JWT → CurrentUser             (401)
    get_account       → + row of accounts             (403 account_missing)
    require_status()  → + allowed statuses            (403 account_<status>)

Errors are returned as ``{"detail": {"code": "...", "message": "..."}}`` so
that the front can branch on ``detail.code``.
"""

import uuid
from collections.abc import Callable, Iterator
from typing import Annotated

import psycopg2
from fastapi import Depends, HTTPException, status

from app.auth.dependency import get_current_user
from app.auth.schemas import CurrentUser
from app.services.accounts import Account, AccountRepository, AccountStatus
from app.services.db import get_connection

_STATUS_MESSAGES = {
    AccountStatus.PENDING: "Your account is waiting for approval",
    AccountStatus.REJECTED: "Your account request was not accepted",
    AccountStatus.SUSPENDED: "Your account is suspended",
    AccountStatus.APPROVED: "Your account is approved",
}


def forbidden(code: str, message: str) -> HTTPException:
    """Build a 403 whose ``detail.code`` tells the front what to display."""
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": code, "message": message},
    )


def get_accounts() -> Iterator[AccountRepository]:
    """Provide an ``AccountRepository`` on a connection closed after the request.

    Raises:
        HTTPException: 503 if the database cannot be reached.

    """
    try:
        conn = get_connection()
    except psycopg2.OperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from exc
    try:
        yield AccountRepository(conn)
    finally:
        conn.close()


def get_account(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    accounts: Annotated[AccountRepository, Depends(get_accounts)],
) -> Account:
    """Return the account of the authenticated user, whatever its status.

    Also records the user's activity (``last_seen_at``, at most once a minute).

    Raises:
        HTTPException: 403 ``account_missing`` if the user has no account
            (or the token subject is not a user id).

    """
    try:
        uuid.UUID(user.id)
    except ValueError:
        account = None
    else:
        account = accounts.get(user.id)
    if account is None:
        raise forbidden("account_missing", "No account exists for this user")
    accounts.touch_last_seen(user.id)
    return account


def require_status(*allowed: AccountStatus) -> Callable[..., Account]:
    """Build a dependency that only lets accounts with an allowed status through.

    Usage: ``account: Annotated[Account, Depends(require_status(APPROVED))]``.

    The 403 code is ``account_<status>`` (e.g. ``account_pending``).
    """

    def dependency(account: Annotated[Account, Depends(get_account)]) -> Account:
        if account.status not in allowed:
            raise forbidden(
                f"account_{account.status}", _STATUS_MESSAGES[account.status]
            )
        return account

    return dependency
