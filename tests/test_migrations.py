"""Unit tests for the API migrations: file discovery and the command line.

No database: files are written to a temporary folder, and the command line is
run against a fake connection and patched service functions.
"""

from pathlib import Path

import pytest

from app import migrate
from app.services.migrations import (
    MIGRATIONS_DIR,
    Migration,
    MigrationError,
    MigrationMode,
    MigrationRun,
    MigrationStatus,
    discover_migrations,
)


def _write(directory: Path, name: str, content: str = "SELECT 1;") -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


class TestDiscoverMigrations:
    """File naming rules and ordering."""

    def test_files_are_listed_in_number_order(self, tmp_path: Path) -> None:
        """Migrations come back sorted by number, with version and name."""
        _write(tmp_path, "A002__second.sql")
        _write(tmp_path, "A001__first.sql")

        result = discover_migrations(tmp_path)

        assert [(m.version, m.name) for m in result] == [
            ("A001", "first"),
            ("A002", "second"),
        ]

    def test_privileged_suffix_is_detected(self, tmp_path: Path) -> None:
        """The .privileged.sql suffix marks an administrator-only migration."""
        _write(tmp_path, "A001__accounts.sql")
        _write(tmp_path, "A002__signup_hook.privileged.sql")

        result = discover_migrations(tmp_path)

        assert [m.privileged for m in result] == [False, True]
        assert result[1].name == "signup_hook"

    def test_non_sql_files_are_ignored(self, tmp_path: Path) -> None:
        """A README next to the migrations is not a migration."""
        _write(tmp_path, "README.md", "# doc")
        _write(tmp_path, "A001__first.sql")

        assert [m.version for m in discover_migrations(tmp_path)] == ["A001"]

    @pytest.mark.parametrize(
        "name",
        [
            "V001__cardiolab_style.sql",
            "A1__too_short.sql",
            "A001_single_underscore.sql",
            "A001__Upper_Case.sql",
            "A001__.sql",
        ],
    )
    def test_badly_named_sql_file_is_rejected(self, tmp_path: Path, name: str) -> None:
        """A .sql file that does not follow the naming rule stops everything."""
        _write(tmp_path, name)

        with pytest.raises(MigrationError, match="Invalid migration file name"):
            discover_migrations(tmp_path)

    def test_duplicate_numbers_are_rejected(self, tmp_path: Path) -> None:
        """Two files cannot share a number, whatever their kind."""
        _write(tmp_path, "A001__first.sql")
        _write(tmp_path, "A001__other.privileged.sql")

        with pytest.raises(MigrationError, match="Duplicate migration numbers: A001"):
            discover_migrations(tmp_path)

    def test_bundled_migrations_folder_is_valid(self) -> None:
        """The migrations shipped with the API follow the rules."""
        discover_migrations(MIGRATIONS_DIR)


class TestMigration:
    """Checksum and permissions of one migration."""

    def test_checksum_changes_when_the_file_changes(self, tmp_path: Path) -> None:
        """Editing a file changes its checksum (how edits are detected)."""
        path = _write(tmp_path, "A001__first.sql", "SELECT 1;")
        migration = discover_migrations(tmp_path)[0]
        before = migration.checksum

        path.write_text("SELECT 2;", encoding="utf-8")

        assert migration.checksum != before

    @pytest.mark.parametrize(
        ("privileged", "mode", "allowed"),
        [
            (False, MigrationMode.APP, True),
            (False, MigrationMode.PRIVILEGED, False),
            (True, MigrationMode.APP, False),
            (True, MigrationMode.PRIVILEGED, True),
            (False, MigrationMode.ALL, True),
            (True, MigrationMode.ALL, True),
        ],
    )
    def test_allowed_in(
        self, privileged: bool, mode: MigrationMode, allowed: bool
    ) -> None:
        """Each mode applies only its own kind, except ALL."""
        migration = Migration("A001", "x", privileged, Path("A001__x.sql"))

        assert migration.allowed_in(mode) is allowed


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class TestCommandLine:
    """python -m app.migrate: mode selection, output and exit codes."""

    @pytest.fixture
    def conn(self, monkeypatch: pytest.MonkeyPatch) -> _FakeConnection:
        """Replace the database connection with a fake one."""
        fake = _FakeConnection()
        monkeypatch.setattr(migrate, "get_connection", lambda: fake)
        return fake

    @pytest.fixture
    def calls(self, monkeypatch: pytest.MonkeyPatch) -> list[MigrationMode]:
        """Record the mode of each run_migrations call, applying nothing."""
        recorded: list[MigrationMode] = []

        def fake_run(conn, *, schema, mode):
            recorded.append(mode)
            return MigrationRun()

        monkeypatch.setattr(migrate, "run_migrations", fake_run)
        return recorded

    @pytest.mark.parametrize(
        ("argv", "mode"),
        [
            ([], MigrationMode.APP),
            (["--privileged"], MigrationMode.PRIVILEGED),
            (["--all"], MigrationMode.ALL),
        ],
    )
    def test_options_select_the_mode(
        self,
        conn: _FakeConnection,
        calls: list[MigrationMode],
        argv: list[str],
        mode: MigrationMode,
    ) -> None:
        """Each option runs the migrations in the matching mode."""
        assert migrate.main(argv) == 0
        assert calls == [mode]
        assert conn.closed

    def test_options_are_mutually_exclusive(self, conn: _FakeConnection) -> None:
        """--privileged and --all cannot be combined."""
        with pytest.raises(SystemExit):
            migrate.main(["--privileged", "--all"])

    def test_applied_and_waiting_migrations_are_reported(
        self,
        conn: _FakeConnection,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The output lists what was applied and what needs another run."""
        applied = Migration("A001", "accounts", False, Path("A001__accounts.sql"))
        waiting = Migration("A002", "hook", True, Path("A002__hook.privileged.sql"))
        monkeypatch.setattr(
            migrate,
            "run_migrations",
            lambda conn, *, schema, mode: MigrationRun([applied], waiting),
        )

        assert migrate.main([]) == 0

        out = capsys.readouterr().out
        assert "Applied A001  accounts" in out
        assert "python -m app.migrate --privileged" in out

    def test_nothing_to_apply_is_reported(
        self, conn: _FakeConnection, calls: list, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An up-to-date database says so."""
        migrate.main([])

        assert "No migration to apply." in capsys.readouterr().out

    def test_status_lists_applied_and_pending(
        self,
        conn: _FakeConnection,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--status prints one line per migration and applies nothing."""
        from datetime import UTC, datetime

        first = Migration("A001", "accounts", False, Path("A001__accounts.sql"))
        second = Migration("A002", "hook", True, Path("A002__hook.privileged.sql"))
        monkeypatch.setattr(
            migrate,
            "migration_status",
            lambda conn, *, schema: [
                MigrationStatus(first, datetime(2026, 11, 3, 10, 30, tzinfo=UTC)),
                MigrationStatus(second, None),
            ],
        )
        monkeypatch.setattr(
            migrate, "run_migrations", lambda *a, **k: pytest.fail("must not apply")
        )

        assert migrate.main(["--status"]) == 0

        out = capsys.readouterr().out
        assert "A001  accounts  applied 2026-11-03 10:30" in out
        assert "A002  hook (privileged)  pending" in out

    def test_migration_error_exits_with_1(
        self,
        conn: _FakeConnection,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Invalid or edited migrations end the command with code 1."""

        def fail(conn, *, schema, mode):
            raise MigrationError("edited")

        monkeypatch.setattr(migrate, "run_migrations", fail)

        assert migrate.main([]) == 1
        assert "Migration error: edited" in capsys.readouterr().err
        assert conn.closed

    def test_failing_sql_exits_with_1(
        self,
        conn: _FakeConnection,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A migration failing in the database ends the command with code 1."""

        def fail(conn, *, schema, mode):
            raise RuntimeError("syntax error")

        monkeypatch.setattr(migrate, "run_migrations", fail)

        assert migrate.main([]) == 1
        assert "rolled back: syntax error" in capsys.readouterr().err

    def test_connection_failure_exits_with_1(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An unreachable database ends the command with code 1."""

        def refuse():
            raise OSError("connection refused")

        monkeypatch.setattr(migrate, "get_connection", refuse)

        assert migrate.main([]) == 1
        assert "Cannot connect" in capsys.readouterr().err
