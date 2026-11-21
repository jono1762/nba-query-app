"""Checks that the real migrations build the expected NBA schema."""

import psycopg
import pytest

from nba_query_app.db.migrate import apply_migrations

EXPECTED_TABLES = {"teams", "players", "games", "player_game_stats", "meta"}
EXPECTED_VIEWS = {"player_season_stats", "stat_coverage", "stat_tracked_since"}


@pytest.fixture
def migrated(db: psycopg.Connection) -> psycopg.Connection:
    apply_migrations(db)
    return db


def insert_sample_game(conn: psycopg.Connection) -> None:
    conn.execute("INSERT INTO nba.teams VALUES (1, 'OKC', 'Oklahoma City Thunder', '2026-10-20')")
    conn.execute("INSERT INTO nba.teams VALUES (2, 'SAS', 'San Antonio Spurs', '2026-10-20')")
    conn.execute("INSERT INTO nba.players VALUES (10, 'Jaylin Williams', '2026-10-20')")
    conn.execute(
        "INSERT INTO nba.games VALUES "
        "('0022600003', '2026-10-20', '2026-27', 'Regular Season', 2, 1)"
    )


def test_all_tables_exist(migrated: psycopg.Connection) -> None:
    rows = migrated.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'nba' AND table_type = 'BASE TABLE'"
    ).fetchall()
    assert {name for (name,) in rows} == EXPECTED_TABLES


def test_all_views_exist(migrated: psycopg.Connection) -> None:
    rows = migrated.execute(
        "SELECT table_name FROM information_schema.views WHERE table_schema = 'nba'"
    ).fetchall()
    assert {name for (name,) in rows} == EXPECTED_VIEWS


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


def test_play_in_is_a_valid_season_type(migrated: psycopg.Connection) -> None:
    insert_sample_game(migrated)
    migrated.execute(
        "INSERT INTO nba.games VALUES ('0052500001', '2026-04-14', '2025-26', 'Play-In', 1, 2)"
    )


def test_unrecorded_stats_may_be_null(migrated: psycopg.Connection) -> None:
    # Like a 1946-47 box score: points and makes only.
    insert_sample_game(migrated)
    migrated.execute(
        """
        INSERT INTO nba.player_game_stats (game_id, player_id, team_id, team_abbreviation,
            opponent_team_id, is_home, won, pts, fgm, ftm)
        VALUES ('0022600003', 10, 1, 'OKC', 2, false, true, 12, 5, 2)
        """
    )
    row = migrated.execute("SELECT minutes, reb, blk FROM nba.player_game_stats").fetchone()
    assert row == (None, None, None)


@pytest.mark.parametrize("column", ["pts", "fgm", "ftm"])
def test_always_recorded_stats_are_required(migrated: psycopg.Connection, column: str) -> None:
    insert_sample_game(migrated)
    values = {"pts": 12, "fgm": 5, "ftm": 2, column: None}
    with pytest.raises(psycopg.errors.NotNullViolation):
        migrated.execute(
            """
            INSERT INTO nba.player_game_stats (game_id, player_id, team_id, team_abbreviation,
                opponent_team_id, is_home, won, pts, fgm, ftm)
            VALUES ('0022600003', 10, 1, 'OKC', 2, false, true, %(pts)s, %(fgm)s, %(ftm)s)
            """,
            values,
        )


def test_nba_cup_final_is_a_valid_season_type(migrated: psycopg.Connection) -> None:
    insert_sample_game(migrated)
    migrated.execute(
        "INSERT INTO nba.games VALUES "
        "('0062400001', '2024-12-17', '2024-25', 'NBA Cup Final', 1, 2)"
    )


def test_final_round_only_on_cup_final_games(migrated: psycopg.Connection) -> None:
    insert_sample_game(migrated)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrated.execute("UPDATE nba.games SET nba_cup_round = 'Final'")  # a regular-season game


def test_cup_final_game_cannot_have_another_round(migrated: psycopg.Connection) -> None:
    insert_sample_game(migrated)
    migrated.execute("UPDATE nba.games SET season_type = 'NBA Cup Final'")
    with pytest.raises(psycopg.errors.CheckViolation):
        migrated.execute("UPDATE nba.games SET nba_cup_round = 'Semifinal'")
