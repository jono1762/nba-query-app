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


def group_stage(teams: list[int]) -> list[tuple[str, str, int, int]]:
    """Each team plays 4 group games (a round robin over 5 teams, so every team plays 4)."""
    games, number = [], 0
    for i, a in enumerate(teams):
        for b in teams[i + 1 :]:
            number += 1
            games.append((f"g{number:02d}", f"2024-11-{number:02d}", a, b))
    return games


def test_rounds_follow_each_teams_game_count() -> None:
    games = group_stage([1, 2, 3, 4, 5]) + [
        ("qf1", "2024-12-10", 1, 2),
        ("qf2", "2024-12-10", 3, 4),
        ("sf1", "2024-12-14", 1, 3),
        ("final", "2024-12-17", 1, 6),
    ]
    # Teams 6-10 form a second group, so team 6 reaches the final with its own QF and SF.
    games += [
        ("g6a", "2024-11-20", 6, 7),
        ("g6b", "2024-11-21", 6, 8),
        ("g6c", "2024-11-22", 6, 9),
        ("g6d", "2024-11-23", 6, 10),
        ("g7a", "2024-11-24", 7, 8),
        ("g7b", "2024-11-25", 7, 9),
        ("g7c", "2024-11-26", 7, 10),
        ("g8a", "2024-11-27", 8, 9),
        ("g8b", "2024-11-28", 8, 10),
        ("g9a", "2024-11-29", 9, 10),
        ("qf3", "2024-12-11", 6, 7),
        ("qf4", "2024-12-11", 8, 9),
        ("sf2", "2024-12-14", 6, 8),
    ]
    rounds = cup_rounds(response(games, final="final"))

    assert rounds["g01"] == "Group" and rounds["g6a"] == "Group"
    assert {rounds[g] for g in ("qf1", "qf2", "qf3", "qf4")} == {"Quarterfinal"}
    assert {rounds[g] for g in ("sf1", "sf2")} == {"Semifinal"}
    assert rounds["final"] == "Final"
    assert list(rounds.values()).count("Group") == 20


def test_tournament_in_progress_is_all_group_games() -> None:
    # Like 2026-27 in November: 2-3 games per team so far.
    games = [("g1", "2026-11-01", 1, 2), ("g2", "2026-11-05", 1, 3), ("g3", "2026-11-08", 2, 3)]
    assert set(cup_rounds(response(games)).values()) == {"Group"}


def test_teams_disagreeing_about_the_round_is_an_error() -> None:
    # Team 1 has 4 earlier games (so this is its quarterfinal), team 9 has none.
    games = group_stage([1, 2, 3, 4, 5]) + [("odd", "2024-12-10", 1, 9)]
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
