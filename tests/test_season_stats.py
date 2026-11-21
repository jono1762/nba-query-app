"""The nba.player_season_stats view: season totals calculated from game rows."""

from typing import Any

import psycopg
from conftest import NbaData


def season_row(conn: psycopg.Connection, player_id: int, season_type: str) -> dict[str, Any]:
    cur = conn.execute(
        "SELECT * FROM nba.player_season_stats WHERE player_id = %s AND season_type = %s",
        (player_id, season_type),
    )
    columns = [c.name for c in cur.description]
    rows = cur.fetchall()
    assert len(rows) == 1
    return dict(zip(columns, rows[0], strict=True))


def test_adds_up_a_players_games(nba_data: NbaData) -> None:
    nba_data.player(7, "Test Player")
    nba_data.game("g1")
    nba_data.game("g2")
    nba_data.stats("g1", 7, 1, won=True, pts=20, fgm=8, ftm=4, reb=10, minutes=30)
    nba_data.stats("g2", 7, 1, won=False, pts=11, fgm=5, ftm=1, reb=3, minutes=25)

    row = season_row(nba_data.conn, 7, "Regular Season")

    assert row["full_name"] == "Test Player"
    assert row["season"] == "2000-01"
    assert (row["games"], row["pts"], row["fgm"], row["ftm"]) == (2, 31, 13, 5)
    assert (row["reb"], row["minutes"]) == (13, 55)


def test_total_is_null_unless_recorded_in_every_game(nba_data: NbaData) -> None:
    nba_data.game("g1")
    nba_data.game("g2")
    nba_data.stats("g1", 7, 1, won=True, blk=3, ast=2)
    nba_data.stats("g2", 7, 1, won=False, blk=None, ast=4)

    row = season_row(nba_data.conn, 7, "Regular Season")

    assert row["blk"] is None  # 3 would undercount: g2's blocks weren't recorded
    assert row["ast"] == 6


def test_regular_season_and_playoffs_are_separate_rows(nba_data: NbaData) -> None:
    nba_data.game("g1")
    nba_data.game("p1", season_type="Playoffs")
    nba_data.stats("g1", 7, 1, won=True, pts=10)
    nba_data.stats("p1", 7, 1, won=True, pts=30)

    assert season_row(nba_data.conn, 7, "Regular Season")["pts"] == 10
    assert season_row(nba_data.conn, 7, "Playoffs")["pts"] == 30


def test_traded_player_has_one_row_listing_both_teams(nba_data: NbaData) -> None:
    nba_data.team(1, "HOU")
    nba_data.team(3, "LAL")
    nba_data.game("g1", home=1, away=2)
    nba_data.game("g2", home=3, away=2)
    nba_data.stats("g1", 7, 1, won=True, pts=10, abbreviation="HOU")
    nba_data.stats("g2", 7, 3, won=True, pts=12, abbreviation="LAL")

    row = season_row(nba_data.conn, 7, "Regular Season")

    assert (row["games"], row["pts"], row["team_abbreviations"]) == (2, 22, "HOU, LAL")


def test_playing_for_both_teams_in_one_game_counts_one_game(nba_data: NbaData) -> None:
    # The protested 1978 game, finished after a trade: two rows, one game played.
    nba_data.game("g1", home=1, away=2)
    nba_data.stats("g1", 7, 1, won=True, pts=4)
    nba_data.stats("g1", 7, 2, won=False, pts=6)

    row = season_row(nba_data.conn, 7, "Regular Season")

    assert (row["games"], row["pts"]) == (1, 10)
