"""FastAPI dependencies that combine authentication and the database.

``app/auth/`` only verifies the JWT (no SQL); this module chains it with the
account read from the database. Order of the chain::

    get_current_user  → JWT → CurrentUser             (401)
    get_account       → + row of accounts             (403 account_missing)
    require_status()  → + allowed statuses            (403 account_<status>)
    require_admin     → + approved and role admin     (403 account_<status>, admin_only)

One database connection per request (``get_db``), shared by the repositories.

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
from app.services.accounts import (
    Account,
    AccountRepository,
    AccountRole,
    AccountStatus,
)
from app.services.audit import AuditRepository
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


def get_db() -> Iterator:
    """Provide a database connection, closed after the request.

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
        yield conn
    finally:
        conn.close()


def get_accounts(conn: Annotated[object, Depends(get_db)]) -> AccountRepository:
    """Provide an ``AccountRepository`` on the request's connection."""
    return AccountRepository(conn)


def get_audit_log(conn: Annotated[object, Depends(get_db)]) -> AuditRepository:
    """Provide an ``AuditRepository`` on the request's connection."""
    return AuditRepository(conn)


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


def require_admin(account: Annotated[Account, Depends(get_account)]) -> Account:
    """Let only approved administrators through.

    The role is read from ``accounts.role`` on every request, never from the
    JWT. A suspended administrator loses access at once.

    Raises:
        HTTPException: 403 ``account_<status>`` if the account is not approved,
            403 ``admin_only`` if it is not an administrator.

    """
    if account.status is not AccountStatus.APPROVED:
        raise forbidden(f"account_{account.status}", _STATUS_MESSAGES[account.status])
    if account.role is not AccountRole.ADMIN:
        raise forbidden("admin_only", "Reserved to administrators")
    return account
