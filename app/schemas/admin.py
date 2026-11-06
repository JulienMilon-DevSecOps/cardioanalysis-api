"""Administration endpoints request and response schemas.

No health data in any of them: administrators see accounts, never measurements,
scores or HRV profiles.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

AccountStatusValue = Literal["pending", "approved", "rejected", "suspended"]
AccountActionValue = Literal["approve", "reject", "suspend", "reactivate"]


class AdminAccountRecord(BaseModel):
    """One account in the administration list."""

    user_id: str
    display_name: str
    status: AccountStatusValue
    role: Literal["user", "admin"]
    created_at: datetime
    status_changed_at: datetime | None = None
    last_seen_at: datetime | None = None


class AdminAccountResponse(AdminAccountRecord):
    """Detail of an account (beta survey and optional consents: later issues)."""

    status_changed_by: str | None = None
    onboarding_completed: bool


class AdminAccountPage(BaseModel):
    """A page of accounts and the total matching the filters."""

    items: list[AdminAccountRecord]
    total: int
    limit: int
    offset: int


class BulkActionRequest(BaseModel):
    """Body of ``POST /v1/admin/accounts/bulk``: one action on several accounts."""

    ids: list[UUID] = Field(min_length=1, max_length=100)
    action: AccountActionValue


class BulkActionResult(BaseModel):
    """Outcome for one account of a bulk action.

    ``code`` (when ``ok`` is false): ``account_not_found``,
    ``invalid_transition`` or ``own_account``.
    """

    user_id: str
    ok: bool
    status: AccountStatusValue | None = None
    code: str | None = None


class BulkActionResponse(BaseModel):
    """Results of a bulk action, one per distinct id, in request order."""

    results: list[BulkActionResult]


class AuditRecord(BaseModel):
    """One entry of the administration journal."""

    id: int
    at: datetime
    actor_id: str | None = None
    action: str
    target: str | None = None
    details: dict


class AuditPage(BaseModel):
    """A page of journal entries, newest first."""

    items: list[AuditRecord]
    total: int
    limit: int
    offset: int
