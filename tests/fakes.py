"""In-memory stand-ins shared by the unit tests (no database)."""

from dataclasses import replace
from datetime import UTC, datetime

from app.services.accounts import (
    Account,
    AccountExistsError,
    AccountRole,
    AccountStatus,
)


def make_account(
    user_id: str,
    *,
    status: AccountStatus = AccountStatus.APPROVED,
    role: AccountRole = AccountRole.USER,
    display_name: str = "Test",
) -> Account:
    """Build an account as the database would return it."""
    return Account(
        user_id=user_id,
        display_name=display_name,
        status=status,
        role=role,
        created_at=datetime(2026, 11, 3, tzinfo=UTC),
        status_changed_at=None,
        onboarding_completed_at=None,
        last_seen_at=None,
    )


class FakeAccountRepository:
    """Same interface as ``AccountRepository``, backed by a dict."""

    def __init__(self, *accounts: Account) -> None:
        """Start with the given accounts."""
        self.accounts = {a.user_id: a for a in accounts}
        self.touched: list[str] = []

    def get(self, user_id: str) -> Account | None:
        """Return the account, or ``None``."""
        return self.accounts.get(user_id)

    def list_accounts(self, status: AccountStatus | None = None) -> list[Account]:
        """Return the accounts, optionally of one status."""
        return [a for a in self.accounts.values() if status in (None, a.status)]

    def create(
        self,
        user_id: str,
        display_name: str,
        *,
        status: AccountStatus = AccountStatus.PENDING,
        role: AccountRole = AccountRole.USER,
    ) -> Account:
        """Create an account; refuse a duplicate."""
        if user_id in self.accounts:
            raise AccountExistsError(user_id)
        account = make_account(
            user_id, status=status, role=role, display_name=display_name
        )
        self.accounts[user_id] = account
        return account

    def _update(self, user_id: str, **changes) -> Account | None:
        if user_id not in self.accounts:
            return None
        self.accounts[user_id] = replace(self.accounts[user_id], **changes)
        return self.accounts[user_id]

    def set_role(self, user_id: str, role: AccountRole) -> Account | None:
        """Change the role."""
        return self._update(user_id, role=role)

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
