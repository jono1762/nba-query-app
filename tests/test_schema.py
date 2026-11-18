"""Checks that the real migrations build the expected NBA schema."""

import psycopg
import pytest

from nba_query.db.migrate import apply_migrations

EXPECTED_TABLES = {"teams", "players", "games", "player_game_stats", "meta"}


@pytest.fixture
def migrated(db: psycopg.Connection) -> psycopg.Connection:
    apply_migrations(db)
    return db


def insert_sample_game(conn: psycopg.Connection) -> None:
    conn.execute("INSERT INTO nba.teams VALUES (1, 'OKC', 'Oklahoma City Thunder')")
    conn.execute("INSERT INTO nba.teams VALUES (2, 'SAS', 'San Antonio Spurs')")
    conn.execute("INSERT INTO nba.players VALUES (10, 'Jaylin Williams')")
    conn.execute(
        "INSERT INTO nba.games VALUES "
        "('0022600003', '2026-10-20', '2026-27', 'Regular Season', 2, 1)"
    )


def test_all_tables_exist(migrated: psycopg.Connection) -> None:
    rows = migrated.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'nba'"
    ).fetchall()
    assert {name for (name,) in rows} == EXPECTED_TABLES


def test_meta_starts_with_one_row(migrated: psycopg.Connection) -> None:
    assert migrated.execute("SELECT data_version, latest_game_date FROM nba.meta").fetchall() == [
        (0, None)
    ]


def test_accepts_a_valid_box_score_row(migrated: psycopg.Connection) -> None:
    insert_sample_game(migrated)
    migrated.execute(
        """
        INSERT INTO nba.player_game_stats VALUES
            ('0022600003', 10, 1, 'OKC', 2, false, true,
             17, 5, 1, 6, 1, 5, 2, 2, 1, 3, 4, 1, 0, 0, 1, 1, -4)
        """
    )
    assert migrated.execute("SELECT pts FROM nba.player_game_stats").fetchone() == (5,)


@pytest.mark.parametrize(
    "bad_insert",
    [
        "INSERT INTO nba.games VALUES ('g1', '2026-10-20', '2026', 'Regular Season', 1, 2)",
        "INSERT INTO nba.games VALUES ('g1', '2026-10-20', '2026-27', 'Preseason', 1, 2)",
        "INSERT INTO nba.games VALUES ('g1', '2026-10-20', '2026-27', 'Regular Season', 1, 1)",
    ],
    ids=["bad season format", "unknown season type", "team plays itself"],
)
def test_rejects_invalid_games(migrated: psycopg.Connection, bad_insert: str) -> None:
    insert_sample_game(migrated)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrated.execute(bad_insert)


def test_rejects_more_makes_than_attempts(migrated: psycopg.Connection) -> None:
    insert_sample_game(migrated)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrated.execute(
            """
            INSERT INTO nba.player_game_stats VALUES
                ('0022600003', 10, 1, 'OKC', 2, false, true,
                 17, 5, 7, 6, 1, 5, 2, 2, 1, 3, 4, 1, 0, 0, 1, 1, -4)
            """
        )
