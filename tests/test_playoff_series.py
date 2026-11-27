"""The nba.playoff_series view and the playoff-round columns on nba.games."""

import psycopg
import pytest
from conftest import NbaData


def add_series(
    nba_data: NbaData, prefix: str, home: int, away: int, results: str, rnd: tuple[int, str]
) -> None:
    """One game per letter in results: 'H' = home team won, 'A' = away team won."""
    number, name = rnd
    for i, result in enumerate(results, start=1):
        game_id = f"{prefix}{i}"
        nba_data.game(
            game_id,
            "2015-16",
            "Playoffs",
            home=home,
            away=away,
            game_date=f"2016-06-{i:02d}",
            playoff_round=(number, name, i),
        )
        home_pts, away_pts = (100, 90) if result == "H" else (90, 100)
        nba_data.team_stats(game_id, home, pts=home_pts, opponent_pts=away_pts)
        nba_data.team_stats(game_id, away, pts=away_pts, opponent_pts=home_pts)


def test_one_row_per_series_with_winner_and_score(nba_data: NbaData) -> None:
    # The 2016 Finals: Cleveland (1) came back from 3-1 down against Golden State (2).
    add_series(nba_data, "f", 2, 1, "HHAHAAA", (4, "Finals"))

    rows = nba_data.conn.execute(
        "SELECT playoff_round_name, winner_team_id, loser_team_id, winner_wins, loser_wins, "
        "games FROM nba.playoff_series"
    ).fetchall()

    assert rows == [("Finals", 1, 2, 4, 3, 7)]


def test_a_tied_series_in_progress_still_has_one_row(nba_data: NbaData) -> None:
    add_series(nba_data, "s", 1, 2, "HA", (1, "First Round"))

    rows = nba_data.conn.execute(
        "SELECT winner_team_id, winner_wins, loser_wins FROM nba.playoff_series"
    ).fetchall()

    assert rows == [(1, 1, 1)]


def test_round_robin_games_are_not_a_series(nba_data: NbaData) -> None:
    # 1953-54: a round robin (here one game), then the Finals.
    nba_data.game("rr", "1953-54", "Playoffs", home=1, away=2, playoff_round=(1, "Round Robin", 1))
    nba_data.team_stats("rr", 1, pts=90, opponent_pts=80)
    nba_data.team_stats("rr", 2, pts=80, opponent_pts=90)
    nba_data.game("f1", "1953-54", "Playoffs", home=1, away=2, playoff_round=(2, "Finals", 1))
    nba_data.team_stats("f1", 1, pts=70, opponent_pts=75)
    nba_data.team_stats("f1", 2, pts=75, opponent_pts=70)

    rows = nba_data.conn.execute(
        "SELECT playoff_round_name, winner_team_id FROM nba.playoff_series"
    ).fetchall()

    assert rows == [("Finals", 2)]


def test_only_playoff_games_can_have_a_round(nba_data: NbaData) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        nba_data.game("g1", "2015-16", "Regular Season", playoff_round=(1, "First Round", 1))
