from pathlib import Path

import pytest

from gsoi_assistant.core.errors import DatabaseConfigError
from gsoi_assistant.db.base import check_database_url


def test_creates_missing_parent_folder(tmp_path: Path) -> None:
    target = tmp_path / "new" / "dir" / "app.db"
    check_database_url(f"sqlite+aiosqlite:///{target}")
    assert target.exists()


def test_directory_instead_of_file_gives_clear_error(tmp_path: Path) -> None:
    with pytest.raises(DatabaseConfigError, match="is a folder"):
        check_database_url(f"sqlite+aiosqlite:///{tmp_path}")


def test_unwritable_location_names_the_absolute_path(tmp_path: Path) -> None:
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    with pytest.raises(DatabaseConfigError, match="cannot open the SQLite database file"):
        check_database_url(f"sqlite+aiosqlite:///{blocker}/sub/app.db")


@pytest.mark.parametrize(
    "url",
    ["sqlite+aiosqlite://", "sqlite+aiosqlite:///:memory:", "postgresql+asyncpg://u:p@h/db"],
)
def test_non_file_urls_are_ignored(url: str) -> None:
    check_database_url(url)
