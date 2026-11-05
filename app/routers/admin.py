"""Administration endpoints: accounts and journal, approved administrators only.

Every route depends on ``require_admin``. Status changes are checked and
journaled by the account service; this router only maps its errors to HTTP:
404 ``account_not_found``, 409 ``invalid_transition`` / ``own_account``.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import get_accounts, get_audit_log, require_admin
from app.schemas.admin import (
    AccountStatusValue,
    AdminAccountPage,
    AdminAccountRecord,
    AdminAccountResponse,
    AuditPage,
    AuditRecord,
    BulkActionRequest,
    BulkActionResponse,
    BulkActionResult,
)
from app.services.accounts import (
    Account,
    AccountAction,
    AccountNotFoundError,
    AccountRepository,
    AccountStatus,
    InvalidTransitionError,
    OwnAccountError,
)
from app.services.audit import AuditRepository

router = APIRouter(prefix="/admin", tags=["admin"])

Admin = Annotated[Account, Depends(require_admin)]
Accounts = Annotated[AccountRepository, Depends(get_accounts)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def _error(status_code: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message, **extra}
    )


def _record(account: Account) -> AdminAccountRecord:
    return AdminAccountRecord(
        user_id=account.user_id,
        display_name=account.display_name,
        status=account.status,
        role=account.role,
        created_at=account.created_at,
        status_changed_at=account.status_changed_at,
        last_seen_at=account.last_seen_at,
    )


def _detail(account: Account) -> AdminAccountResponse:
    return AdminAccountResponse(
        **_record(account).model_dump(),
        status_changed_by=account.status_changed_by,
        onboarding_completed=account.onboarding_completed,
    )


@router.get("/accounts")
def list_accounts(
    admin: Admin,
    accounts: Accounts,
    account_status: Annotated[AccountStatusValue | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AdminAccountPage:
    """List accounts, oldest first, filtered by status and search.

    ``q`` searches the display name and the start of the user id (emails are
    not searchable: the API cannot read Supabase Auth's users).
    """
    items, total = accounts.search(
        status=AccountStatus(account_status) if account_status else None,
        q=q,
        limit=limit,
        offset=offset,
    )
    return AdminAccountPage(
        items=[_record(a) for a in items], total=total, limit=limit, offset=offset
    )


@router.get("/accounts/{user_id}")
def read_account(
    user_id: UUID, admin: Admin, accounts: Accounts
) -> AdminAccountResponse:
    """Return one account."""
    account = accounts.get(str(user_id))
    if account is None:
        raise _error(
            status.HTTP_404_NOT_FOUND, "account_not_found", "No account for this user"
        )
    return _detail(account)


def _change_status(
    accounts: AccountRepository, user_id: str, action: AccountAction, admin: Account
) -> Account:
    """Apply an action, mapping the service errors to HTTP errors."""
    try:
        return accounts.change_status(user_id, action, actor_id=admin.user_id)
    except AccountNotFoundError as exc:
        raise _error(
            status.HTTP_404_NOT_FOUND, "account_not_found", "No account for this user"
        ) from exc
    except OwnAccountError as exc:
        raise _error(
            status.HTTP_409_CONFLICT,
            "own_account",
            "Administrators cannot change their own status",
        ) from exc
    except InvalidTransitionError as exc:
        raise _error(
            status.HTTP_409_CONFLICT,
            "invalid_transition",
            str(exc),
            current_status=str(exc.current),
        ) from exc


@router.post("/accounts/{user_id}/approve")
def approve_account(
    user_id: UUID, admin: Admin, accounts: Accounts
) -> AdminAccountResponse:
    """Approve a pending account."""
    return _detail(_change_status(accounts, str(user_id), AccountAction.APPROVE, admin))


@router.post("/accounts/{user_id}/reject")
def reject_account(
    user_id: UUID, admin: Admin, accounts: Accounts
) -> AdminAccountResponse:
    """Reject a pending account."""
    return _detail(_change_status(accounts, str(user_id), AccountAction.REJECT, admin))


@router.post("/accounts/{user_id}/suspend")
def suspend_account(
    user_id: UUID, admin: Admin, accounts: Accounts
) -> AdminAccountResponse:
    """Suspend an approved account: its access is cut at the next request."""
    return _detail(_change_status(accounts, str(user_id), AccountAction.SUSPEND, admin))


@router.post("/accounts/{user_id}/reactivate")
def reactivate_account(
    user_id: UUID, admin: Admin, accounts: Accounts
) -> AdminAccountResponse:
    """Reactivate a suspended account."""
    return _detail(
        _change_status(accounts, str(user_id), AccountAction.REACTIVATE, admin)
    )


@router.post("/accounts/bulk")
def bulk_action(
    body: BulkActionRequest, admin: Admin, accounts: Accounts
) -> BulkActionResponse:
    """Apply one action to several accounts, each one independently.

    An account that cannot change (already processed, missing, own account)
    does not stop the others: each result says what happened.
    """
    action = AccountAction(body.action)
    results: list[BulkActionResult] = []
    for user_id in dict.fromkeys(str(i) for i in body.ids):
        try:
            account = accounts.change_status(user_id, action, actor_id=admin.user_id)
        except AccountNotFoundError:
            results.append(
                BulkActionResult(user_id=user_id, ok=False, code="account_not_found")
            )
        except OwnAccountError:
            results.append(
                BulkActionResult(user_id=user_id, ok=False, code="own_account")
            )
        except InvalidTransitionError as exc:
            results.append(
                BulkActionResult(
                    user_id=user_id,
                    ok=False,
                    status=exc.current,
                    code="invalid_transition",
                )
            )
        else:
            results.append(
                BulkActionResult(user_id=user_id, ok=True, status=account.status)
            )
    return BulkActionResponse(results=results)


@router.get("/audit")
def list_audit(
    admin: Admin,
    journal: Annotated[AuditRepository, Depends(get_audit_log)],
    action: Annotated[str | None, Query(max_length=50)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AuditPage:
    """Return the administration journal, newest first (``action`` filter)."""
    entries, total = journal.list_entries(action=action, limit=limit, offset=offset)
    return AuditPage(
        items=[AuditRecord(**vars(e)) for e in entries],
        total=total,
        limit=limit,
        offset=offset,
    )
