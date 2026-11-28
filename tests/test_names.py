"""Player names stored without accents (names.py and migration 019)."""

import shutil
from pathlib import Path

import psycopg
import pytest

from nba_query_app.db.migrate import MIGRATIONS_DIR, apply_migrations
from nba_query_app.names import strip_accents

ACCENTED = {
    "Luka Dončić": "Luka Doncic",
    "Kristaps Porziņģis": "Kristaps Porzingis",
    "Egor Dëmin": "Egor Demin",
    "Yanic Konan Niederhäuser": "Yanic Konan Niederhauser",
    "Pacôme Dadiet": "Pacome Dadiet",
    "Anžejs Pasečņiks": "Anzejs Pasecniks",
    "Vít Krejčí": "Vit Krejci",
    "Shaquille O’Neal": "Shaquille O'Neal",
    "Kristian Ødegaard": "Kristian Odegaard",
}


@pytest.mark.parametrize(("name", "plain"), ACCENTED.items())
def test_strip_accents(name: str, plain: str) -> None:
    assert strip_accents(name) == plain


def test_plain_names_are_unchanged() -> None:
    assert strip_accents("Shaquille O'Neal-Abdul Jr.") == "Shaquille O'Neal-Abdul Jr."


# Every letter migration 019 handles, one per player name.
LETTERS = [chr(cp) for cp in range(0xC0, 0x180) if chr(cp) not in "×÷"] + ["‘", "’"]


def test_migration_strips_stored_names_like_the_loader(
    db: psycopg.Connection, tmp_path: Path
) -> None:
    before = tmp_path / "before"
    before.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "019":
            shutil.copy(path, before)
    apply_migrations(db, before)
    names = [f"Player {c}x" for c in LETTERS] + ["LeBron James"]
    db.cursor().executemany(
        "INSERT INTO nba.players VALUES (%s, %s, '2000-01-01')", list(enumerate(names, 1))
    )
    version = db.execute("SELECT data_version FROM nba.meta").fetchone()[0]

    apply_migrations(db)

    stored = dict(db.execute("SELECT player_id, full_name FROM nba.players").fetchall())
    assert [stored[i] for i in range(1, len(names) + 1)] == [strip_accents(n) for n in names]
    assert db.execute("SELECT data_version FROM nba.meta").fetchone()[0] == version + 1


def test_names_with_accents_are_refused(nba_data) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        nba_data.player(1, "Luka Dončić")
