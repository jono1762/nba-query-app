import json
from datetime import date
from pathlib import Path

import pytest

from nba_query_app.ingest.normalize import (
    OPTIONAL_STATS,
    REQUIRED_STATS,
    NormalizeError,
    _resolve_duplicate_player_rows,
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
        ("62024", ("2024-25", "NBA Cup Final")),
        ("21946", ("1946-47", "Regular Season")),
    ],
)
def test_season_from_id(season_id: str, expected: tuple[str, str]) -> None:
    assert season_from_id(season_id) == expected


@pytest.mark.parametrize("season_id", ["12026", "32026", "72026", "2202", "2abcd"])
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
    set_column(response, "WL", "T")
    with pytest.raises(NormalizeError, match="WL"):
        normalize_league_game_log(response)


def set_wl_for_team(response: dict, game_id: str, team_id: int, value) -> None:
    headers = response["resultSets"][0]["headers"]
    for row in response["resultSets"][0]["rowSet"]:
        if row[headers.index("GAME_ID")] == game_id and row[headers.index("TEAM_ID")] == team_id:
            row[headers.index("WL")] = value


def test_missing_win_loss_on_one_player_comes_from_teammates(response: dict) -> None:
    # Real example: 1959-60 box scores with W/L missing on single player rows.
    set_column(response, "WL", None)  # row 0 is a PHI player; PHI won
    row = normalize_league_game_log(response).stats[0]
    assert row.won is True


def test_missing_win_loss_for_a_whole_team_comes_from_opponent(response: dict) -> None:
    set_wl_for_team(response, "0022600001", BOS, None)
    stats = normalize_league_game_log(response).stats
    assert all(s.won is False for s in stats if s.team_id == BOS)  # DET won


def test_no_win_loss_for_either_team_is_an_error(response: dict) -> None:
    set_wl_for_team(response, "0022600001", BOS, None)
    set_wl_for_team(response, "0022600001", DET, None)
    with pytest.raises(NormalizeError, match="No W/L"):
        normalize_league_game_log(response)


def test_teammates_disagreeing_about_the_result_is_an_error(response: dict) -> None:
    set_column(response, "WL", "L", row_index=0)  # one PHI player says L, the rest W
    with pytest.raises(NormalizeError, match="disagree about who won"):
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


def test_a_mislabelled_row_is_outvoted_by_teammates(response: dict) -> None:
    # Real example: 1967-68 SFW at SDR, where 10 rows say '@' and 2 say 'vs.'.
    set_column(response, "MATCHUP", "PHI vs. NYK", row_index=0)  # row 0 is PHI, really away
    game = next(g for g in normalize_league_game_log(response).games if g.game_id == "0022600002")
    assert (game.home_team_id, game.away_team_id) == (NYK, PHI)


def test_a_tied_home_away_vote_is_an_error(response: dict) -> None:
    headers = response["resultSets"][0]["headers"]
    rows = response["resultSets"][0]["rowSet"]
    phi_rows = [i for i, r in enumerate(rows) if r[headers.index("TEAM_ID")] == PHI]
    if len(phi_rows) % 2:  # make the count even so an exact tie is possible
        rows.pop(phi_rows[-1])
        phi_rows = phi_rows[:-1]
    for i in phi_rows[: len(phi_rows) // 2]:
        set_column(response, "MATCHUP", "PHI vs. NYK", row_index=i)

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


# --- Player listed on both teams in one game ---------------------------------


def raw_row(game_id: str, team_id: int, day: str, pts: int = 0, player_id: int = 7) -> dict:
    """A minimal raw row, as produced from the response, for the duplicate helpers."""
    row = {"GAME_ID": game_id, "PLAYER_ID": player_id, "TEAM_ID": team_id, "GAME_DATE": day}
    row.update({source: 0 for source in [*REQUIRED_STATS.values(), *OPTIONAL_STATS.values()]})
    row["PTS"] = pts
    return row


def test_copy_with_real_stats_wins() -> None:
    real, empty = raw_row("g1", BOS, "1951-12-01", pts=13), raw_row("g1", DET, "1951-12-01")
    assert _resolve_duplicate_player_rows([empty, real]) == [real]


def test_copy_with_points_beats_copy_with_only_stray_stats() -> None:
    # Real example: Bob Cousy 1951-52, 13 pts for BOS; for BAL 0 pts but 1 FT attempted.
    real = raw_row("g1", BOS, "1951-12-01", pts=13)
    ghost = raw_row("g1", DET, "1951-12-01")
    ghost["FTA"] = 1
    assert _resolve_duplicate_player_rows([ghost, real]) == [real]


def test_identical_copies_use_team_from_nearest_other_game() -> None:
    chi = raw_row("g2", BOS, "1979-12-05", pts=17)
    hou = raw_row("g2", DET, "1979-12-05", pts=17)
    earlier = raw_row("g1", DET, "1979-12-01", pts=10)  # 4 days before: on DET
    later = raw_row("g3", BOS, "1980-03-01", pts=8)  # months later: on BOS

    result = _resolve_duplicate_player_rows([chi, hou, earlier, later])
    assert hou in result and chi not in result


def test_undecidable_all_zero_copies_are_dropped() -> None:
    # Real example: John Murphy 1946-47, all-zero rows for both teams, no other games.
    rows = [raw_row("g1", BOS, "1946-12-01"), raw_row("g1", DET, "1946-12-01")]
    assert _resolve_duplicate_player_rows(rows) == []


def test_undecidable_copies_with_stats_are_an_error() -> None:
    rows = [raw_row("g1", BOS, "1979-12-05", pts=17), raw_row("g1", DET, "1979-12-05", pts=17)]
    with pytest.raises(NormalizeError, match="Can't tell which team"):
        _resolve_duplicate_player_rows(rows)


def test_different_scoring_for_each_team_is_genuine_and_both_kept() -> None:
    # Real example: Eric Money in the protested 76ers-Nets game of 1978-11-08,
    # finished after his trade: 23 pts for NJN and 4 pts for PHL.
    nets, sixers = raw_row("g1", BOS, "1978-11-08", pts=23), raw_row("g1", DET, "1978-11-08", pts=4)
    assert _resolve_duplicate_player_rows([nets, sixers]) == [nets, sixers]


def test_zero_point_copy_with_minutes_is_genuine() -> None:
    # Real example: Ralph Simpson in the same 1978 game: 8 pts for PHL, and for NJN
    # 2 minutes and 2 assists but 0 points. Both are real.
    sixers = raw_row("g1", DET, "1978-11-08", pts=8)
    nets = raw_row("g1", BOS, "1978-11-08")
    nets.update({"MIN": 2, "AST": 2})
    assert _resolve_duplicate_player_rows([nets, sixers]) == [nets, sixers]


def test_most_complete_copy_of_one_performance_wins() -> None:
    # Real example: John Shumate 1977-78, 16 pts for both DET (full box score) and BUF (partial).
    full = raw_row("g1", DET, "1977-12-30", pts=16)
    full.update({"MIN": 38, "FGA": 7, "REB": 7})
    partial = raw_row("g1", BOS, "1977-12-30", pts=16)
    assert _resolve_duplicate_player_rows([partial, full]) == [full]


def test_duplicate_in_a_real_response_is_resolved(response: dict) -> None:
    headers = response["resultSets"][0]["headers"]
    rows = response["resultSets"][0]["rowSet"]
    bos_row = next(r for r in rows if r[headers.index("TEAM_ID")] == BOS)
    ghost = list(bos_row)  # same player, wrongly also listed for DET with no stats
    for column, value in [
        ("TEAM_ID", DET),
        ("TEAM_ABBREVIATION", "DET"),
        ("MATCHUP", "DET vs. BOS"),
    ]:
        ghost[headers.index(column)] = value
    for column in [*REQUIRED_STATS.values(), *OPTIONAL_STATS.values()]:
        ghost[headers.index(column)] = 0
    rows.append(ghost)

    stats = normalize_league_game_log(response).stats
    player = bos_row[headers.index("PLAYER_ID")]
    assert [s.team_id for s in stats if s.player_id == player] == [BOS]
