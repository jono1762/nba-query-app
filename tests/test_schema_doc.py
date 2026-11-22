"""docs/SCHEMA.md must describe every table, view and column in the nba schema."""

import re
from pathlib import Path

import psycopg
import pytest

from nba_query_app.db.migrate import apply_migrations

SCHEMA_DOC = Path(__file__).parents[1] / "docs" / "SCHEMA.md"


def doc_sections() -> dict[str, str]:
    """Each "### `nba.<name>`" heading's name, mapped to the text under it."""
    parts = re.split(r"^### `nba\.(\w+)`$", SCHEMA_DOC.read_text(), flags=re.M)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


@pytest.fixture
def columns(db: psycopg.Connection) -> dict[str, list[str]]:
    apply_migrations(db)
    rows = db.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'nba' ORDER BY table_name, ordinal_position"
    ).fetchall()
    result: dict[str, list[str]] = {}
    for table, column in rows:
        result.setdefault(table, []).append(column)
    return result


def test_every_table_and_view_has_a_section(columns: dict[str, list[str]]) -> None:
    assert set(doc_sections()) == set(columns)


def test_every_column_is_described_in_its_section(columns: dict[str, list[str]]) -> None:
    sections = doc_sections()
    missing = [
        f"{table}.{column}"
        for table, names in columns.items()
        for column in names
        if f"`{column}`" not in sections.get(table, "")
    ]
    assert missing == []
