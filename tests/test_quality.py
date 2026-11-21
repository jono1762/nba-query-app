"""Data-quality checks, run against small hand-made games."""

import re

import pytest
from conftest import NbaData

from nba_query_app.quality import CHECKS, KnownException, game_ids_in, report, run_checks

CURRENT = "2026-27"


def add_clean_game(
    nba_data: NbaData,
    game_id: str,
    season: str = "2000-01",
    *,
    home: int = 1,
    away: int = 2,
    home_won: bool = True,
    season_type: str = "Regular Season",
    nba_cup_round: str | None = None,
) -> None:
    """A plausible game: 5 players a side, 48 minutes each, the winner scoring more."""
    nba_data.game(game_id, season, season_type, home=home, away=away, nba_cup_round=nba_cup_round)
    for team, won in ((home, home_won), (away, not home_won)):
        for i in range(5):
            fgm = 9 if won else 8  # 2-pointers only: 18 or 16 points each
            nba_data.stats(game_id, team * 100 + i, team, won=won, pts=2 * fgm, fgm=fgm, minutes=48)


def violations(nba_data: NbaData, check_name: str) -> list[tuple[str, str]]:
    [check] = [c for c in CHECKS if c.name == check_name]
    [result] = run_checks(nba_data.conn, CURRENT, [check], exceptions=[])
    return result.violations


def keys(nba_data: NbaData, check_name: str) -> list[str]:
    return [key for key, _detail in violations(nba_data, check_name)]


def test_clean_data_passes_every_check(nba_data: NbaData) -> None:
    # 1960-61: before 82-game schedules, so two games per team is not "short".
    add_clean_game(nba_data, "g1", "1960-61")
    add_clean_game(nba_data, "g2", "1960-61", home=2, away=1)

    results = run_checks(nba_data.conn, CURRENT, exceptions=[])

    assert {r.check.name: r.violations for r in results} == {c.name: [] for c in CHECKS}


def test_three_pointers_before_the_line(nba_data: NbaData) -> None:
    nba_data.game("g1", "1978-79")
    nba_data.stats("g1", 7, 1, won=True, pts=3, fgm=1, fg3m=1)
    nba_data.game("g2", "1979-80")
    nba_data.stats("g2", 8, 1, won=True, pts=3, fgm=1, fg3m=1)

    assert keys(nba_data, "three_pointers_before_the_line") == ["g1 7"]


@pytest.mark.parametrize(
    ("season", "season_type", "cup_round", "reported"),
    [
        ("2018-19", "Play-In", None, True),
        ("2019-20", "Play-In", None, False),
        ("2022-23", "Regular Season", "Group", True),
        ("2023-24", "Regular Season", "Group", False),
    ],
)
def test_game_type_before_it_existed(
    nba_data: NbaData, season: str, season_type: str, cup_round: str | None, reported: bool
) -> None:
    nba_data.game("g1", season, season_type, nba_cup_round=cup_round)

    assert keys(nba_data, "game_type_before_it_existed") == (["g1"] if reported else [])


def test_stat_disappears(nba_data: NbaData) -> None:
    for season, blk in [("1983-84", 1), ("1984-85", None), ("1985-86", 2)]:
        nba_data.game(f"g{season}", season)
        nba_data.stats(f"g{season}", 7, 1, won=True, blk=blk)

    assert keys(nba_data, "stat_disappears") == ["blk 1984-85"]


def test_points_dont_add_up(nba_data: NbaData) -> None:
    nba_data.game("g1", "2000-01")
    nba_data.stats("g1", 7, 1, won=True, pts=9, fgm=3, fg3m=1, ftm=2)  # 2*2 + 3 + 2 = 9
    nba_data.stats("g1", 8, 1, won=True, pts=10, fgm=3, fg3m=1, ftm=2)
    nba_data.game("g2", "1960-61")  # no 3-point line, so FG3M is NULL and counts as 0
    nba_data.stats("g2", 9, 1, won=True, pts=8, fgm=3, ftm=2, fg3m=None, fg3a=None)
    nba_data.stats("g2", 10, 1, won=True, pts=9, fgm=3, ftm=2, fg3m=None, fg3a=None)

    assert keys(nba_data, "points_dont_add_up") == ["g1 8", "g2 10"]


def test_points_check_skips_unrecorded_three_pointers_after_the_line(nba_data: NbaData) -> None:
    nba_data.game("g1", "1980-81")
    nba_data.stats("g1", 7, 1, won=True, pts=9, fgm=3, ftm=2, fg3m=None, fg3a=None)

    assert keys(nba_data, "points_dont_add_up") == []


def test_parts_exceed_whole(nba_data: NbaData) -> None:
    nba_data.game("g1")
    nba_data.stats("g1", 7, 1, won=True, reb=5, oreb=2, dreb=2)
    nba_data.stats("g1", 8, 1, won=True, pts=3, fgm=1, fga=1, fg3m=1, fg3a=2)
    nba_data.stats("g1", 9, 1, won=True, reb=5, oreb=2, dreb=3)

    assert keys(nba_data, "parts_exceed_whole") == ["g1 7", "g1 8"]


def test_above_single_game_record(nba_data: NbaData) -> None:
    nba_data.game("g1")
    nba_data.stats("g1", 7, 1, won=True, reb=56)
    nba_data.stats("g1", 8, 1, won=True, reb=55)  # equals the record: fine

    assert keys(nba_data, "above_single_game_record") == ["g1 7 reb"]


def test_winner_with_fewer_points(nba_data: NbaData) -> None:
    add_clean_game(nba_data, "g1")
    nba_data.conn.execute("UPDATE nba.player_game_stats SET won = NOT won WHERE game_id = 'g1'")

    assert keys(nba_data, "result_doesnt_match_points") == ["g1"]


def test_two_winners(nba_data: NbaData) -> None:
    add_clean_game(nba_data, "g1")
    nba_data.conn.execute("UPDATE nba.player_game_stats SET won = true WHERE game_id = 'g1'")

    assert keys(nba_data, "result_doesnt_match_points") == ["g1"]


def test_team_minutes_short(nba_data: NbaData) -> None:
    add_clean_game(nba_data, "g1")
    nba_data.conn.execute(
        "UPDATE nba.player_game_stats SET minutes = 30 WHERE game_id = 'g1' AND player_id = 100"
    )

    expected = [("g1 1", "2000-01 T1: 222 of 240 minutes")]
    assert violations(nba_data, "team_minutes_short") == expected


def test_team_minutes_rounding_is_not_reported(nba_data: NbaData) -> None:
    add_clean_game(nba_data, "g1")
    nba_data.conn.execute(
        "UPDATE nba.player_game_stats SET minutes = 37 WHERE game_id = 'g1' AND player_id = 100"
    )

    assert keys(nba_data, "team_minutes_short") == []


def test_team_minutes_allow_for_overtime(nba_data: NbaData) -> None:
    add_clean_game(nba_data, "g1")  # 240 each; give both teams 25 overtime minutes
    nba_data.conn.execute(
        "UPDATE nba.player_game_stats SET minutes = 53 WHERE game_id = 'g1' AND player_id % 100 = 0"
    )
    assert keys(nba_data, "team_minutes_short") == []

    nba_data.conn.execute(  # the home team's overtime minutes go missing
        "UPDATE nba.player_game_stats SET minutes = 48 WHERE game_id = 'g1' AND player_id = 100"
    )
    assert keys(nba_data, "team_minutes_short") == []  # 260 vs 265: rounding-sized
    nba_data.conn.execute(
        "UPDATE nba.player_game_stats SET minutes = 30 WHERE game_id = 'g1' AND player_id = 100"
    )
    assert keys(nba_data, "team_minutes_short") == ["g1 1"]  # 242 vs 265


def test_uneven_games_per_team(nba_data: NbaData) -> None:
    for i, (home, away) in enumerate([(1, 2), (3, 4), (1, 3), (2, 4), (1, 4)]):
        add_clean_game(nba_data, f"g{i}", home=home, away=away)

    # Team 1 played 3, team 4 played 3, teams 2 and 3 played 2.
    [(season, detail)] = violations(nba_data, "uneven_games_per_team")
    assert season == "2000-01"
    assert detail in ("T1 3, T4 3 (most teams 2)", "T2 2, T3 2 (most teams 3)")


def test_uneven_games_skips_the_current_season(nba_data: NbaData) -> None:
    add_clean_game(nba_data, "g1", CURRENT, home=1, away=2)
    add_clean_game(nba_data, "g2", CURRENT, home=1, away=3)

    assert keys(nba_data, "uneven_games_per_team") == []


def test_short_schedule(nba_data: NbaData) -> None:
    add_clean_game(nba_data, "g1", "1999-00")
    add_clean_game(nba_data, "g2", "1966-67")  # before 82-game schedules: not checked
    add_clean_game(nba_data, "g3", CURRENT)

    assert violations(nba_data, "short_schedule") == [("1999-00", "most teams played 1")]


def test_cup_final_team_must_have_won_a_semifinal(nba_data: NbaData) -> None:
    add_clean_game(nba_data, "s1", "2024-25", home=1, away=2, nba_cup_round="Semifinal")
    add_clean_game(
        nba_data, "s2", "2024-25", home=3, away=4, home_won=False, nba_cup_round="Semifinal"
    )
    add_clean_game(
        nba_data,
        "f1",
        "2024-25",
        home=1,
        away=4,
        season_type="NBA Cup Final",
        nba_cup_round="Final",
    )
    assert keys(nba_data, "cup_final_teams") == []

    nba_data.conn.execute("UPDATE nba.player_game_stats SET won = NOT won WHERE game_id = 's2'")
    assert violations(nba_data, "cup_final_teams") == [("f1", "2024-25 T4")]


def test_known_exceptions_are_excused_not_reported(nba_data: NbaData) -> None:
    nba_data.game("g1", "1978-79")
    nba_data.stats("g1", 7, 1, won=True, pts=3, fgm=1, fg3m=1)
    nba_data.stats("g1", 8, 1, won=True, pts=3, fgm=1, fg3m=1)
    exception = KnownException("three_pointers_before_the_line", r"g1 7", "checked by hand")
    [check] = [c for c in CHECKS if c.name == "three_pointers_before_the_line"]

    [result] = run_checks(nba_data.conn, CURRENT, [check], [exception])

    assert [key for key, _ in result.violations] == ["g1 8"]
    assert [(key, reason) for key, _, reason in result.excused] == [("g1 7", "checked by hand")]


def test_report_counts_unexplained_violations(nba_data: NbaData) -> None:
    nba_data.game("g1", "1978-79")
    nba_data.stats("g1", 7, 1, won=True, pts=3, fgm=1, fg3m=1)
    lines: list[str] = []
    checks = [c for c in CHECKS if c.name in ("three_pointers_before_the_line", "stat_disappears")]

    found = report(run_checks(nba_data.conn, CURRENT, checks, exceptions=[]), log=lines.append)

    assert found == 1
    assert lines[0].startswith("[1 found] three_pointers_before_the_line")
    assert lines[2].startswith("[ok] stat_disappears")
    assert lines[-1] == "Data-quality checks: 1 unexplained violation(s)."


def test_game_ids_in_matches_only_those_seasons() -> None:
    pattern = game_ids_in("1946-47", "1963-64")

    assert re.fullmatch(pattern, "0024600028")  # 1946-47 regular season
    assert re.fullmatch(pattern, "0046300001")  # 1963-64 playoffs
    assert not re.fullmatch(pattern, "0026400001")  # 1964-65


def test_team_minutes_skip_teams_with_no_minutes_and_note_partial_gaps(
    nba_data: NbaData,
) -> None:
    add_clean_game(nba_data, "g1")
    nba_data.conn.execute(
        "UPDATE nba.player_game_stats SET minutes = NULL WHERE game_id = 'g1' AND team_id = 1"
    )
    assert keys(nba_data, "team_minutes_short") == []  # nothing recorded: a coverage gap

    nba_data.conn.execute(
        "UPDATE nba.player_game_stats SET minutes = 48 WHERE game_id = 'g1' AND player_id <> 100"
    )
    for i in range(2, 7):  # enough complete games that minutes count as fully recorded
        add_clean_game(nba_data, f"g{i}")
    expected = [("g1 1", "2000-01 T1: 192 of 240 minutes (1 player(s) without minutes)")]
    assert violations(nba_data, "team_minutes_short") == expected
