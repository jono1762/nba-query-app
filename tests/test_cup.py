"""Working out NBA Cup rounds from the IST response. No network: responses are built here."""

import pytest

from nba_query_app.ingest.cup import CupError, cup_rounds, final_only

HEADERS = ["SEASON_ID", "TEAM_ID", "GAME_ID", "GAME_DATE"]


def response(games: list[tuple[str, str, int, int]], final: str | None = None) -> dict:
    """games: (game_id, date, team, team). One row per team (players don't matter here)."""
    rows = []
    for game_id, day, team_a, team_b in games:
        season_id = "62024" if game_id == final else "22024"
        rows += [[season_id, team_a, game_id, day], [season_id, team_b, game_id, day]]
    return {"resultSets": [{"headers": HEADERS, "rowSet": rows}]}


def group_stage(group: str, teams: list[int]) -> list[tuple[str, str, int, int]]:
    """A round robin over 5 teams, so each plays 4 group games. IDs are like 'A01'."""
    games = []
    pairs = [(a, b) for i, a in enumerate(teams) for b in teams[i + 1 :]]
    for number, (a, b) in enumerate(pairs, start=1):
        games.append((f"{group}{number:02d}", f"2024-11-{number:02d}", a, b))
    return games


def test_rounds_follow_each_teams_game_count() -> None:
    # Two groups of 5 teams (4 group games each), then a bracket, all in date order.
    knockouts = [
        ("qf1", "2024-12-10", 1, 2),
        ("qf2", "2024-12-10", 3, 4),
        ("qf3", "2024-12-11", 6, 7),
        ("qf4", "2024-12-11", 8, 9),
        ("sf1", "2024-12-14", 1, 3),  # winners of qf1 and qf2
        ("sf2", "2024-12-14", 6, 8),  # winners of qf3 and qf4
        ("final", "2024-12-17", 1, 6),
    ]
    games = group_stage("A", [1, 2, 3, 4, 5]) + group_stage("B", [6, 7, 8, 9, 10]) + knockouts

    rounds = cup_rounds(response(games, final="final"))

    assert list(rounds.values()).count("Group") == 20
    assert rounds["A01"] == "Group" and rounds["B10"] == "Group"
    assert {rounds[g] for g in ("qf1", "qf2", "qf3", "qf4")} == {"Quarterfinal"}
    assert {rounds[g] for g in ("sf1", "sf2")} == {"Semifinal"}
    assert rounds["final"] == "Final"


def test_tournament_in_progress_is_all_group_games() -> None:
    # Like 2026-27 in November: 2-3 games per team so far.
    games = [("g1", "2026-11-01", 1, 2), ("g2", "2026-11-05", 1, 3), ("g3", "2026-11-08", 2, 3)]
    assert set(cup_rounds(response(games)).values()) == {"Group"}


def test_teams_disagreeing_about_the_round_is_an_error() -> None:
    # Team 1 has 4 earlier games (so this is its quarterfinal), team 9 has none.
    games = group_stage("A", [1, 2, 3, 4, 5]) + [("odd", "2024-12-10", 1, 9)]
    with pytest.raises(CupError, match="disagree about the round"):
        cup_rounds(response(games))


def test_seventh_game_must_be_the_marked_final() -> None:
    games = [("g1", "2024-11-01", 1, 2)]
    with pytest.raises(CupError, match="marked as the final"):
        cup_rounds(response(games, final="g1"))  # a group game marked with prefix 6


def test_final_only_keeps_the_championship_rows() -> None:
    games = [("g1", "2024-11-01", 1, 2), ("final", "2024-12-17", 1, 2)]
    result = final_only(response(games, final="final"))
    assert {row[2] for row in result["resultSets"][0]["rowSet"]} == {"final"}
