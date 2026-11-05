"""In-memory stand-ins shared by the unit tests (no database)."""

from dataclasses import replace
from datetime import UTC, datetime

from app.services.accounts import (
    Account,
    AccountAction,
    AccountExistsError,
    AccountNotFoundError,
    AccountRole,
    AccountStatus,
    next_status,
)
from app.services.audit import AuditEntry


def make_account(
    user_id: str,
    *,
    status: AccountStatus = AccountStatus.APPROVED,
    role: AccountRole = AccountRole.USER,
    display_name: str = "Test",
    created_at: datetime | None = None,
) -> Account:
    """Build an account as the database would return it."""
    return Account(
        user_id=user_id,
        display_name=display_name,
        status=status,
        role=role,
        created_at=created_at or datetime(2026, 11, 3, tzinfo=UTC),
        status_changed_at=None,
        onboarding_completed_at=None,
        last_seen_at=None,
    )


class FakeAuditRepository:
    """Same interface as ``AuditRepository``, backed by a list."""

    def __init__(self) -> None:
        """Start with an empty journal."""
        self.entries: list[AuditEntry] = []

    def add(self, *, actor_id, action: str, target, details: dict) -> None:
        """Append an entry (what the real service does in its transaction)."""
        self.entries.append(
            AuditEntry(
                id=len(self.entries) + 1,
                at=datetime.now(UTC),
                actor_id=actor_id,
                action=action,
                target=target,
                details=details,
            )
        )

    def list_entries(
        self, *, action: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[AuditEntry], int]:
        """Return a page of entries, newest first, and the total."""
        matching = [e for e in reversed(self.entries) if action in (None, e.action)]
        return matching[offset : offset + limit], len(matching)


class FakeAccountRepository:
    """Same interface as ``AccountRepository``, backed by a dict.

    Status changes use the real transition rules (``next_status``) and are
    journaled in ``self.journal``.
    """

    def __init__(self, *accounts: Account, journal: FakeAuditRepository | None = None):
        """Start with the given accounts and an (optionally shared) journal."""
        self.accounts = {a.user_id: a for a in accounts}
        self.journal = journal or FakeAuditRepository()
        self.touched: list[str] = []
        self.audit_sources: list[str | None] = []

    def get(self, user_id: str) -> Account | None:
        """Return the account, or ``None``."""
        return self.accounts.get(user_id)

    def list_accounts(self, status: AccountStatus | None = None) -> list[Account]:
        """Return the accounts, optionally of one status."""
        return self.search(status=status, limit=None)[0]

    def search(
        self,
        *,
        status: AccountStatus | None = None,
        q: str | None = None,
        limit: int | None = 50,
        offset: int = 0,
    ) -> tuple[list[Account], int]:
        """Filter like the real search (name anywhere, id prefix), oldest first."""
        found = [
            a
            for a in sorted(
                self.accounts.values(), key=lambda a: (a.created_at, a.user_id)
            )
            if status in (None, a.status)
            and (
                not q
                or q.lower() in a.display_name.lower()
                or a.user_id.startswith(q.lower())
            )
        ]
        page = found[offset:] if limit is None else found[offset : offset + limit]
        return page, len(found)

    def create(
        self,
        user_id: str,
        display_name: str,
        *,
        status: AccountStatus = AccountStatus.PENDING,
        role: AccountRole = AccountRole.USER,
        audit_source: str | None = None,
    ) -> Account:
        """Create an account; refuse a duplicate."""
        if user_id in self.accounts:
            raise AccountExistsError(user_id)
        account = make_account(
            user_id, status=status, role=role, display_name=display_name
        )
        self.accounts[user_id] = account
        self.audit_sources.append(audit_source)
        return account

    def _update(self, user_id: str, **changes) -> Account | None:
        if user_id not in self.accounts:
            return None
        self.accounts[user_id] = replace(self.accounts[user_id], **changes)
        return self.accounts[user_id]

    def set_role(
        self, user_id: str, role: AccountRole, *, audit_source: str | None = None
    ) -> Account | None:
        """Change the role."""
        self.audit_sources.append(audit_source)
        return self._update(user_id, role=role)

    def change_status(
        self, user_id: str, action: AccountAction, *, actor_id: str
    ) -> Account:
        """Apply the real transition rules and journal the change."""
        account = self.accounts.get(user_id)
        if account is None:
            raise AccountNotFoundError(user_id)
        target = next_status(action, account.status, user_id=user_id, actor_id=actor_id)
        updated = self._update(
            user_id,
            status=target,
            status_changed_at=datetime.now(UTC),
            status_changed_by=actor_id,
        )
        self.journal.add(
            actor_id=actor_id,
            action=f"account.{action}",
            target=user_id,
            details={"from": str(account.status), "to": str(target)},
        )
        return updated

    def update_display_name(self, user_id: str, display_name: str) -> Account | None:
        """Change the display name."""
        return self._update(user_id, display_name=display_name)

    def complete_onboarding(self, user_id: str) -> Account | None:
        """Record the first completion date only."""
        account = self.accounts.get(user_id)
        if account is None or account.onboarding_completed_at is not None:
            return account
        return self._update(user_id, onboarding_completed_at=datetime.now(UTC))

    def touch_last_seen(self, user_id: str) -> None:
        """Record the call."""
        self.touched.append(user_id)
