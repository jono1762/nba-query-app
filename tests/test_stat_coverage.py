"""The nba.stat_coverage and nba.stat_tracked_since views."""

import psycopg
from conftest import NbaData


def add_season(nba_data: NbaData, season: str, blk_values: list[int | None]) -> None:
    """One game in the season, with one player row per given blocks value."""
    game_id = f"g{season}"
    nba_data.game(game_id, season)
    for player_id, blk in enumerate(blk_values, start=1):
        nba_data.stats(game_id, player_id, 1, won=True, blk=blk)


def tracked(conn: psycopg.Connection, stat: str) -> tuple[str | None, str | None]:
    return conn.execute(
        "SELECT first_recorded_season, tracked_since FROM nba.stat_tracked_since WHERE stat = %s",
        (stat,),
    ).fetchone()


def test_coverage_is_the_share_of_rows_with_a_value(nba_data: NbaData) -> None:
    add_season(nba_data, "1979-80", [1, None, 2, None])

    row = nba_data.conn.execute(
        "SELECT player_rows, recorded_rows, pct_recorded FROM nba.stat_coverage "
        "WHERE season = '1979-80' AND stat = 'blk'"
    ).fetchone()

    assert row == (4, 2, 50.0)


def test_tracked_since_is_where_full_coverage_starts(nba_data: NbaData) -> None:
    add_season(nba_data, "1972-73", [None, None])
    add_season(nba_data, "1973-74", [1, None])
    add_season(nba_data, "1974-75", [1, 0])
    add_season(nba_data, "1975-76", [0, 3])

    assert tracked(nba_data.conn, "blk") == ("1973-74", "1974-75")
    assert tracked(nba_data.conn, "pts") == ("1972-73", "1972-73")  # always recorded


def test_a_later_gap_moves_tracked_since_past_it(nba_data: NbaData) -> None:
    add_season(nba_data, "1980-81", [1, 2])
    add_season(nba_data, "1981-82", [1, None])
    add_season(nba_data, "1982-83", [1, 2])

    assert tracked(nba_data.conn, "blk") == ("1980-81", "1982-83")


def test_tracked_since_is_null_if_the_latest_season_falls_short(nba_data: NbaData) -> None:
    add_season(nba_data, "1980-81", [1, 2])
    add_season(nba_data, "1981-82", [1, None])

    assert tracked(nba_data.conn, "blk") == ("1980-81", None)


def test_a_stat_never_recorded_has_no_seasons(nba_data: NbaData) -> None:
    add_season(nba_data, "1950-51", [None, None])

    assert tracked(nba_data.conn, "blk") == (None, None)
