import json
from datetime import date
from pathlib import Path

import pytest

from nba_query_app.ingest.normalize import (
    NormalizeError,
    normalize_league_game_log,
    parse_is_home,
    season_from_id,
)

FIXTURE = Path(__file__).parent / "fixtures" / "leaguegamelog_2026-27_two_games.json"

# Team IDs in the fixture (NBA.com IDs).
BOS, DET, NYK, PHI = 1610612738, 1610612765, 1610612752, 1610612755


@pytest.fixture
def response() -> dict:
    """Two real games from the start of 2026-27: BOS @ DET and PHI @ NYK."""
    return json.loads(FIXTURE.read_text())


def set_column(response: dict, column: str, value, row_index: int = 0) -> None:
    headers = response["resultSets"][0]["headers"]
    response["resultSets"][0]["rowSet"][row_index][headers.index(column)] = value


# --- Small parsers -----------------------------------------------------------


@pytest.mark.parametrize(
    ("season_id", "expected"),
    [
        ("22026", ("2026-27", "Regular Season")),
        ("42025", ("2025-26", "Playoffs")),
        ("21999", ("1999-00", "Regular Season")),
        ("52025", ("2025-26", "Play-In")),
        ("21946", ("1946-47", "Regular Season")),
    ],
)
def test_season_from_id(season_id: str, expected: tuple[str, str]) -> None:
    assert season_from_id(season_id) == expected


@pytest.mark.parametrize("season_id", ["12026", "32026", "62026", "2202", "2abcd"])
def test_season_from_id_rejects_unsupported(season_id: str) -> None:
    with pytest.raises(NormalizeError):
        season_from_id(season_id)


def test_parse_is_home() -> None:
    assert parse_is_home("DET vs. BOS") is True
    assert parse_is_home("BOS @ DET") is False
    with pytest.raises(NormalizeError):
        parse_is_home("BOS - DET")


# --- Whole response ----------------------------------------------------------


def test_counts(response: dict) -> None:
    batch = normalize_league_game_log(response)
    assert len(batch.stats) == 41  # one per row in the fixture
    assert len(batch.games) == 2
    assert len(batch.teams) == 4
    assert len(batch.players) == 41


def test_games_get_home_and_away_from_matchup(response: dict) -> None:
    games = {g.game_id: g for g in normalize_league_game_log(response).games}

    bos_at_det = games["0022600001"]
    assert (bos_at_det.home_team_id, bos_at_det.away_team_id) == (DET, BOS)
    assert bos_at_det.season == "2026-27"
    assert bos_at_det.season_type == "Regular Season"

    phi_at_nyk = games["0022600002"]
    assert (phi_at_nyk.home_team_id, phi_at_nyk.away_team_id) == (NYK, PHI)


def test_box_score_rows_know_opponent_home_and_result(response: dict) -> None:
    stats = normalize_league_game_log(response).stats
    for row in stats:
        if row.game_id == "0022600001":
            assert row.opponent_team_id == (DET if row.team_id == BOS else BOS)
            assert row.is_home == (row.team_id == DET)
            assert row.won == (row.team_id == DET)  # DET won this game


def test_stat_values_are_copied(response: dict) -> None:
    headers = response["resultSets"][0]["headers"]
    first = dict(zip(headers, response["resultSets"][0]["rowSet"][0], strict=True))

    row = normalize_league_game_log(response).stats[0]
    assert row.player_id == first["PLAYER_ID"]
    assert (row.pts, row.reb, row.ast, row.minutes) == (
        first["PTS"],
        first["REB"],
        first["AST"],
        first["MIN"],
    )
    assert row.plus_minus == first["PLUS_MINUS"]


def test_newest_team_name_wins(response: dict) -> None:
    # Move one BOS row and one DET row into an earlier, made-up game where Boston
    # had an old name. The name from the later real game should be kept.
    headers = response["resultSets"][0]["headers"]
    rows = response["resultSets"][0]["rowSet"]
    team_ids = [r[headers.index("TEAM_ID")] for r in rows]
    first_bos, first_det = team_ids.index(BOS), team_ids.index(DET)
    for i in (first_bos, first_det):
        set_column(response, "GAME_ID", "0022600000", row_index=i)
        set_column(response, "GAME_DATE", "2026-10-19", row_index=i)
    set_column(response, "TEAM_NAME", "Old Boston Name", row_index=first_bos)

    teams = {t.team_id: t for t in normalize_league_game_log(response).teams}
    assert teams[BOS].name == "Boston Celtics"
    assert teams[BOS].name_as_of == date(2026, 10, 20)


def test_plus_minus_may_be_missing(response: dict) -> None:
    set_column(response, "PLUS_MINUS", None)
    assert normalize_league_game_log(response).stats[0].plus_minus is None


@pytest.mark.parametrize("column", ["PTS", "FGM", "FTM"])
def test_missing_required_stat_is_an_error(response: dict, column: str) -> None:
    set_column(response, column, None)
    with pytest.raises(NormalizeError, match=column):
        normalize_league_game_log(response)


def test_game_with_one_team_is_an_error(response: dict) -> None:
    headers = response["resultSets"][0]["headers"]
    rows = response["resultSets"][0]["rowSet"]
    response["resultSets"][0]["rowSet"] = [r for r in rows if r[headers.index("TEAM_ID")] != BOS]
    with pytest.raises(NormalizeError, match="0022600001"):
        normalize_league_game_log(response)


def test_rows_disagreeing_about_a_game_is_an_error(response: dict) -> None:
    set_column(response, "GAME_DATE", "2026-01-01")
    with pytest.raises(NormalizeError, match="disagree"):
        normalize_league_game_log(response)


def test_bad_win_loss_is_an_error(response: dict) -> None:
    set_column(response, "WL", None)
    with pytest.raises(NormalizeError, match="WL"):
        normalize_league_game_log(response)


def set_matchups(response: dict, game_id: str, matchup_by_team: dict[int, str]) -> None:
    headers = response["resultSets"][0]["headers"]
    for row in response["resultSets"][0]["rowSet"]:
        if row[headers.index("GAME_ID")] == game_id:
            row[headers.index("MATCHUP")] = matchup_by_team[row[headers.index("TEAM_ID")]]


def test_both_teams_away_means_neutral_site(response: dict) -> None:
    # Real example: 0022600189 lists 'IND @ DEN' and 'DEN @ IND'.
    set_matchups(response, "0022600001", {BOS: "BOS @ DET", DET: "DET @ BOS"})
    batch = normalize_league_game_log(response)

    game = next(g for g in batch.games if g.game_id == "0022600001")
    assert game.neutral_site is True
    assert (game.home_team_id, game.away_team_id) == (None, None)
    rows = [s for s in batch.stats if s.game_id == "0022600001"]
    assert all(s.is_home is None for s in rows)
    assert {s.opponent_team_id for s in rows if s.team_id == BOS} == {DET}


def test_normal_games_are_not_neutral(response: dict) -> None:
    assert not any(g.neutral_site for g in normalize_league_game_log(response).games)


def test_both_teams_home_is_an_error(response: dict) -> None:
    set_matchups(response, "0022600001", {BOS: "BOS vs. DET", DET: "DET vs. BOS"})
    with pytest.raises(NormalizeError, match="Both teams"):
        normalize_league_game_log(response)


def test_one_team_listed_both_home_and_away_is_an_error(response: dict) -> None:
    set_column(response, "MATCHUP", "PHI vs. NYK", row_index=0)  # row 0 is PHI, really away
    with pytest.raises(NormalizeError, match="home/away"):
        normalize_league_game_log(response)


@pytest.mark.parametrize(
    ("column", "field"),
    [("REB", "reb"), ("AST", "ast"), ("BLK", "blk"), ("FG3A", "fg3a"), ("TOV", "tov")],
)
def test_unrecorded_stat_is_kept_as_none(response: dict, column: str, field: str) -> None:
    set_column(response, column, None)
    assert getattr(normalize_league_game_log(response).stats[0], field) is None


def set_minutes_for_game(response: dict, game_id: str, minutes) -> None:
    headers = response["resultSets"][0]["headers"]
    for row in response["resultSets"][0]["rowSet"]:
        if row[headers.index("GAME_ID")] == game_id:
            row[headers.index("MIN")] = minutes


def test_game_where_everyone_has_zero_minutes_means_not_recorded(response: dict) -> None:
    # Early seasons (e.g. 1946-47) report MIN = 0 for every player.
    set_minutes_for_game(response, "0022600001", 0)
    stats = normalize_league_game_log(response).stats

    assert all(s.minutes is None for s in stats if s.game_id == "0022600001")
    assert all(s.minutes is not None for s in stats if s.game_id == "0022600002")


def test_single_zero_minute_player_is_a_real_zero(response: dict) -> None:
    # Modern data: under 30 seconds on court rounds down to 0. That is real.
    set_column(response, "MIN", 0, row_index=0)
    assert normalize_league_game_log(response).stats[0].minutes == 0


@pytest.mark.parametrize(
    ("made", "attempted", "field"),
    [("FTM", "FTA", "fta"), ("FGM", "FGA", "fga"), ("FG3M", "FG3A", "fg3a")],
)
def test_fewer_attempts_than_makes_means_attempts_not_recorded(
    response: dict, made: str, attempted: str, field: str
) -> None:
    # Real example: a 1946-47 box score with 1 free throw made and 0 attempted.
    set_column(response, made, 1)
    set_column(response, attempted, 0)
    if made == "FG3M":
        set_column(response, "FGM", 1)
        set_column(response, "FGA", 1)

    row = normalize_league_game_log(response).stats[0]
    assert getattr(row, field) is None
    assert getattr(row, made.lower()) == 1  # makes are kept


def test_consistent_attempts_are_kept(response: dict) -> None:
    set_column(response, "FTM", 3)
    set_column(response, "FTA", 3)
    assert normalize_league_game_log(response).stats[0].fta == 3
