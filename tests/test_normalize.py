import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from nba_query_app.ingest.normalize import (
    OPTIONAL_STATS,
    REQUIRED_STATS,
    NormalizeError,
    TeamGameStats,
    _resolve_duplicate_player_rows,
    _teams_without_minutes,
    normalize_league_game_log,
    normalize_team_game_log,
    parse_is_home,
    season_from_id,
)

FIXTURE = Path(__file__).parent / "fixtures" / "leaguegamelog_2026-27_two_games.json"
TEAM_FIXTURE = Path(__file__).parent / "fixtures" / "leaguegamelog_2026-27_two_games_team.json"

# Team IDs in the fixture (NBA.com IDs).
BOS, DET, NYK, PHI = 1610612738, 1610612765, 1610612752, 1610612755


@pytest.fixture
def response() -> dict:
    """Two real games from the start of 2026-27: BOS @ DET and PHI @ NYK."""
    return json.loads(FIXTURE.read_text())


def normalize_without_team_rows(response: dict):
    """Normalize with an empty team log, so every game uses the player rows' own
    home/away and W/L labels (the fallback for games missing from the team log)."""
    empty = json.loads(TEAM_FIXTURE.read_text())
    empty["resultSets"][0]["rowSet"] = []
    return normalize_league_game_log(response, empty)


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
    batch = normalize_without_team_rows(response)
    assert len(batch.stats) == 41  # one per row in the fixture
    assert len(batch.games) == 2
    assert len(batch.teams) == 4
    assert len(batch.players) == 41


def test_clean_data_fires_no_cleanup_rules(response: dict) -> None:
    assert normalize_without_team_rows(response).cleanups == {}


def test_games_get_home_and_away_from_matchup(response: dict) -> None:
    games = {g.game_id: g for g in normalize_without_team_rows(response).games}

    bos_at_det = games["0022600001"]
    assert (bos_at_det.home_team_id, bos_at_det.away_team_id) == (DET, BOS)
    assert bos_at_det.season == "2026-27"
    assert bos_at_det.season_type == "Regular Season"

    phi_at_nyk = games["0022600002"]
    assert (phi_at_nyk.home_team_id, phi_at_nyk.away_team_id) == (NYK, PHI)


def test_box_score_rows_know_opponent_home_and_result(response: dict) -> None:
    stats = normalize_without_team_rows(response).stats
    for row in stats:
        if row.game_id == "0022600001":
            assert row.opponent_team_id == (DET if row.team_id == BOS else BOS)
            assert row.is_home == (row.team_id == DET)
            assert row.won == (row.team_id == DET)  # DET won this game


def test_stat_values_are_copied(response: dict) -> None:
    headers = response["resultSets"][0]["headers"]
    first = dict(zip(headers, response["resultSets"][0]["rowSet"][0], strict=True))

    row = normalize_without_team_rows(response).stats[0]
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

    teams = {t.team_id: t for t in normalize_without_team_rows(response).teams}
    assert teams[BOS].name == "Boston Celtics"
    assert teams[BOS].name_as_of == date(2026, 10, 20)


def test_plus_minus_may_be_missing(response: dict) -> None:
    set_column(response, "PLUS_MINUS", None)
    assert normalize_without_team_rows(response).stats[0].plus_minus is None


@pytest.mark.parametrize("column", ["PTS", "FGM", "FTM"])
def test_missing_required_stat_is_an_error(response: dict, column: str) -> None:
    set_column(response, column, None)
    with pytest.raises(NormalizeError, match=column):
        normalize_without_team_rows(response)


def test_game_with_one_team_is_an_error(response: dict) -> None:
    headers = response["resultSets"][0]["headers"]
    rows = response["resultSets"][0]["rowSet"]
    response["resultSets"][0]["rowSet"] = [r for r in rows if r[headers.index("TEAM_ID")] != BOS]
    with pytest.raises(NormalizeError, match="0022600001"):
        normalize_without_team_rows(response)


def test_rows_disagreeing_about_a_game_is_an_error(response: dict) -> None:
    set_column(response, "GAME_DATE", "2026-01-01")
    with pytest.raises(NormalizeError, match="disagree"):
        normalize_without_team_rows(response)


def test_bad_win_loss_is_an_error(response: dict) -> None:
    set_column(response, "WL", "T")
    with pytest.raises(NormalizeError, match="WL"):
        normalize_without_team_rows(response)


def set_wl_for_team(response: dict, game_id: str, team_id: int, value) -> None:
    headers = response["resultSets"][0]["headers"]
    for row in response["resultSets"][0]["rowSet"]:
        if row[headers.index("GAME_ID")] == game_id and row[headers.index("TEAM_ID")] == team_id:
            row[headers.index("WL")] = value


def test_player_names_are_stored_without_accents(response: dict) -> None:
    # The fixture's real rows include "Hugo González".
    names = {p.full_name for p in normalize_without_team_rows(response).players}
    assert "Hugo Gonzalez" in names
    assert all(name.isascii() for name in names)


def test_missing_win_loss_on_one_player_comes_from_teammates(response: dict) -> None:
    # Real example: 1959-60 box scores with W/L missing on single player rows.
    set_column(response, "WL", None)  # row 0 is a PHI player; PHI won
    batch = normalize_without_team_rows(response)
    assert batch.stats[0].won is True
    assert batch.cleanups == {"win_loss_filled_in": 1}


def test_missing_win_loss_for_a_whole_team_comes_from_opponent(response: dict) -> None:
    set_wl_for_team(response, "0022600001", BOS, None)
    batch = normalize_without_team_rows(response)
    bos_rows = [s for s in batch.stats if s.team_id == BOS]
    assert all(s.won is False for s in bos_rows)  # DET won
    assert batch.cleanups == {"win_loss_filled_in": len(bos_rows)}


def test_no_win_loss_for_either_team_is_an_error(response: dict) -> None:
    set_wl_for_team(response, "0022600001", BOS, None)
    set_wl_for_team(response, "0022600001", DET, None)
    with pytest.raises(NormalizeError, match="No W/L"):
        normalize_without_team_rows(response)


def test_teammates_disagreeing_about_the_result_is_an_error(response: dict) -> None:
    set_column(response, "WL", "L", row_index=0)  # one PHI player says L, the rest W
    with pytest.raises(NormalizeError, match="disagree about who won"):
        normalize_without_team_rows(response)


def set_matchups(response: dict, game_id: str, matchup_by_team: dict[int, str]) -> None:
    headers = response["resultSets"][0]["headers"]
    for row in response["resultSets"][0]["rowSet"]:
        if row[headers.index("GAME_ID")] == game_id:
            row[headers.index("MATCHUP")] = matchup_by_team[row[headers.index("TEAM_ID")]]


def test_both_teams_away_means_neutral_site(response: dict) -> None:
    # Real example: 0022600189 lists 'IND @ DEN' and 'DEN @ IND'.
    set_matchups(response, "0022600001", {BOS: "BOS @ DET", DET: "DET @ BOS"})
    batch = normalize_without_team_rows(response)

    game = next(g for g in batch.games if g.game_id == "0022600001")
    assert game.neutral_site is True
    assert (game.home_team_id, game.away_team_id) == (None, None)
    rows = [s for s in batch.stats if s.game_id == "0022600001"]
    assert all(s.is_home is None for s in rows)
    assert {s.opponent_team_id for s in rows if s.team_id == BOS} == {DET}


def test_normal_games_are_not_neutral(response: dict) -> None:
    assert not any(g.neutral_site for g in normalize_without_team_rows(response).games)


def test_both_teams_home_is_an_error(response: dict) -> None:
    set_matchups(response, "0022600001", {BOS: "BOS vs. DET", DET: "DET vs. BOS"})
    with pytest.raises(NormalizeError, match="Both teams"):
        normalize_without_team_rows(response)


def test_a_mislabelled_row_is_outvoted_by_teammates(response: dict) -> None:
    # Real example: 1967-68 SFW at SDR, where 10 rows say '@' and 2 say 'vs.'.
    set_column(response, "MATCHUP", "PHI vs. NYK", row_index=0)  # row 0 is PHI, really away
    batch = normalize_without_team_rows(response)
    game = next(g for g in batch.games if g.game_id == "0022600002")
    assert (game.home_team_id, game.away_team_id) == (NYK, PHI)
    assert batch.cleanups == {"home_away_outvoted": 1}


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
        normalize_without_team_rows(response)


@pytest.mark.parametrize(
    ("column", "field"),
    [("REB", "reb"), ("AST", "ast"), ("BLK", "blk"), ("FG3A", "fg3a"), ("TOV", "tov")],
)
def test_unrecorded_stat_is_kept_as_none(response: dict, column: str, field: str) -> None:
    set_column(response, column, None)
    assert getattr(normalize_without_team_rows(response).stats[0], field) is None


def set_minutes_for_game(response: dict, game_id: str, minutes) -> None:
    headers = response["resultSets"][0]["headers"]
    for row in response["resultSets"][0]["rowSet"]:
        if row[headers.index("GAME_ID")] == game_id:
            row[headers.index("MIN")] = minutes


def test_game_where_everyone_has_zero_minutes_means_not_recorded(response: dict) -> None:
    # Early seasons (e.g. 1946-47) report MIN = 0 for every player.
    set_minutes_for_game(response, "0022600001", 0)
    batch = normalize_without_team_rows(response)
    game_rows = [s for s in batch.stats if s.game_id == "0022600001"]

    assert all(s.minutes is None for s in game_rows)
    assert all(s.minutes is not None for s in batch.stats if s.game_id == "0022600002")
    assert batch.cleanups == {"zero_minutes_not_recorded": len(game_rows)}


def test_minutes_missing_from_the_source_are_not_counted_as_a_cleanup(response: dict) -> None:
    set_minutes_for_game(response, "0022600001", None)
    assert normalize_without_team_rows(response).cleanups == {}


def column(response: dict, name: str, row_index: int):
    headers = response["resultSets"][0]["headers"]
    return response["resultSets"][0]["rowSet"][row_index][headers.index(name)]


def teammate_of_row_0(response: dict) -> int:
    return next(
        i
        for i in range(1, len(response["resultSets"][0]["rowSet"]))
        if column(response, "TEAM_ID", i) == column(response, "TEAM_ID", 0)
        and column(response, "GAME_ID", i) == column(response, "GAME_ID", 0)
    )


def test_zero_minutes_on_a_team_whose_minutes_add_up_is_a_real_zero(response: dict) -> None:
    # Modern data: under 30 seconds on court rounds down to 0. That is real.
    teammate = teammate_of_row_0(response)
    total = column(response, "MIN", 0) + column(response, "MIN", teammate)
    set_column(response, "MIN", 0, row_index=0)
    set_column(response, "MIN", total, row_index=teammate)  # the team still adds up

    batch = normalize_without_team_rows(response)

    assert batch.stats[0].minutes == 0
    assert batch.cleanups == {}


def test_zero_minutes_on_a_team_well_short_of_the_game_means_not_recorded(
    response: dict,
) -> None:
    # Early seasons: a player with points but 0 minutes, and the team's minutes
    # fall far short of 240. The 0 means "not recorded".
    assert column(response, "MIN", 0) >= 15
    set_column(response, "MIN", 0, row_index=0)

    batch = normalize_without_team_rows(response)

    assert batch.stats[0].minutes is None
    assert batch.stats[teammate_of_row_0(response)].minutes is not None  # real values stay
    assert batch.cleanups == {"zero_minutes_not_recorded": 1}


@pytest.mark.parametrize(
    ("team_minutes", "opponent_minutes", "short"),
    [
        (240, 240, False),
        (226, 240, False),  # 14 short: rounding-sized
        (225, 240, True),  # 15 short
        (251, 265, False),  # one overtime
        (250, 265, True),
        (0, 0, True),  # nothing recorded
    ],
)
def test_which_teams_count_as_short(team_minutes: int, opponent_minutes: int, short: bool) -> None:
    rows = [
        {"GAME_ID": "g", "TEAM_ID": 1, "MIN": team_minutes},
        {"GAME_ID": "g", "TEAM_ID": 1, "MIN": 0},
        {"GAME_ID": "g", "TEAM_ID": 2, "MIN": opponent_minutes},
    ]
    assert ((("g", 1)) in _teams_without_minutes(rows)) is short


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

    batch = normalize_without_team_rows(response)
    row = batch.stats[0]
    assert getattr(row, field) is None
    assert getattr(row, made.lower()) == 1  # makes are kept
    assert batch.cleanups == {"attempts_not_recorded": 1}


def test_consistent_attempts_are_kept(response: dict) -> None:
    set_column(response, "FTM", 3)
    set_column(response, "FTA", 3)
    assert normalize_without_team_rows(response).stats[0].fta == 3


# --- Player listed on both teams in one game ---------------------------------
# These tests call the duplicate helper directly on minimal rows. The helper only
# compares team IDs, so placeholder teams A and B are used. Comments name the real
# case each test is based on.

TEAM_A, TEAM_B = 1, 2


def raw_row(game_id: str, team_id: int, day: str, pts: int = 0, player_id: int = 7) -> dict:
    """A minimal raw row, as produced from the response, for the duplicate helpers."""
    row = {"GAME_ID": game_id, "PLAYER_ID": player_id, "TEAM_ID": team_id, "GAME_DATE": day}
    row.update({source: 0 for source in [*REQUIRED_STATS.values(), *OPTIONAL_STATS.values()]})
    row["PTS"] = pts
    return row


def test_copy_with_real_stats_wins() -> None:
    real = raw_row("g1", TEAM_A, "1951-12-01", pts=13)
    empty = raw_row("g1", TEAM_B, "1951-12-01")
    assert _resolve_duplicate_player_rows([empty, real]) == [real]


def test_copy_with_points_beats_copy_with_only_stray_stats() -> None:
    # Bob Cousy 1951-52: 13 pts for BOS; for BAL 0 pts but 1 FT attempted.
    real = raw_row("g1", TEAM_A, "1951-12-01", pts=13)
    ghost = raw_row("g1", TEAM_B, "1951-12-01")
    ghost["FTA"] = 1
    assert _resolve_duplicate_player_rows([ghost, real]) == [real]


def test_identical_copies_use_team_from_nearest_other_game() -> None:
    # Like Dwight Jones 1979-80 (traded between HOU and CHI that season): the same
    # 17-point line listed for both teams. The nearest other game decides.
    copy_a = raw_row("g2", TEAM_A, "1979-12-05", pts=17)
    copy_b = raw_row("g2", TEAM_B, "1979-12-05", pts=17)
    days_before = raw_row("g1", TEAM_B, "1979-12-01", pts=10)  # 4 days before: team B
    months_later = raw_row("g3", TEAM_A, "1980-03-01", pts=8)  # months later: team A

    result = _resolve_duplicate_player_rows([copy_a, copy_b, days_before, months_later])
    assert copy_b in result and copy_a not in result


def test_undecidable_all_zero_copies_are_dropped() -> None:
    # John Murphy 1946-47: all-zero rows for both teams and no other games.
    rows = [raw_row("g1", TEAM_A, "1946-12-01"), raw_row("g1", TEAM_B, "1946-12-01")]
    assert _resolve_duplicate_player_rows(rows) == []


def test_undecidable_copies_with_stats_are_an_error() -> None:
    rows = [
        raw_row("g1", TEAM_A, "1979-12-05", pts=17),
        raw_row("g1", TEAM_B, "1979-12-05", pts=17),
    ]
    with pytest.raises(NormalizeError, match="Can't tell which team"):
        _resolve_duplicate_player_rows(rows)


def test_different_scoring_for_each_team_is_genuine_and_both_kept() -> None:
    # Eric Money in the protested 76ers-Nets game of 1978-11-08, finished after
    # his trade: 23 pts for NJN and 4 pts for PHL.
    first_team = raw_row("g1", TEAM_A, "1978-11-08", pts=23)
    second_team = raw_row("g1", TEAM_B, "1978-11-08", pts=4)
    assert _resolve_duplicate_player_rows([first_team, second_team]) == [first_team, second_team]


def test_zero_point_copy_with_minutes_is_genuine() -> None:
    # Ralph Simpson in the same 1978 game: 8 pts for PHL, and for NJN 2 minutes
    # and 2 assists but 0 points. Both are real.
    scored = raw_row("g1", TEAM_A, "1978-11-08", pts=8)
    minutes_only = raw_row("g1", TEAM_B, "1978-11-08")
    minutes_only.update({"MIN": 2, "AST": 2})
    assert _resolve_duplicate_player_rows([minutes_only, scored]) == [minutes_only, scored]


def test_most_complete_copy_of_one_performance_wins() -> None:
    # John Shumate 1977-78: 16 pts for both DET (full box score) and BUF (partial).
    full = raw_row("g1", TEAM_A, "1977-12-30", pts=16)
    full.update({"MIN": 38, "FGA": 7, "REB": 7})
    partial = raw_row("g1", TEAM_B, "1977-12-30", pts=16)
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

    batch = normalize_without_team_rows(response)
    player = bos_row[headers.index("PLAYER_ID")]
    assert [s.team_id for s in batch.stats if s.player_id == player] == [BOS]
    assert batch.cleanups == {"ghost_rows_dropped": 1}


@pytest.mark.parametrize("result", ["W", "L"])
def test_both_teams_with_the_same_result_is_an_error(response: dict, result: str) -> None:
    set_wl_for_team(response, "0022600001", BOS, result)
    set_wl_for_team(response, "0022600001", DET, result)
    with pytest.raises(NormalizeError, match="one winner and one loser"):
        normalize_without_team_rows(response)


def test_every_game_has_one_winner_and_one_loser(response: dict) -> None:
    stats = normalize_without_team_rows(response).stats
    for game_id in {s.game_id for s in stats}:
        results = {s.team_id: s.won for s in stats if s.game_id == game_id}
        assert sorted(results.values()) == [False, True]


# --- Team-level log -------------------------------------------------------------


@pytest.fixture
def team_response() -> dict:
    """The team-level rows for the same two games: BOS 113 @ DET 121, PHI 106 @ NYK 100."""
    return json.loads(TEAM_FIXTURE.read_text())


def team_rows(team_response: dict) -> dict[int, TeamGameStats]:
    return {t.team_id: t for t in normalize_team_game_log(team_response)}


def test_team_rows_have_scores_and_totals(team_response: dict) -> None:
    rows = team_rows(team_response)

    det = rows[DET]
    assert (det.game_id, det.opponent_team_id) == ("0022600001", BOS)
    assert (det.is_home, det.won) == (True, True)
    assert (det.pts, det.opponent_pts, det.minutes) == (121, 113, 240)
    assert (det.fgm, det.fga, det.fg3m, det.ftm, det.reb, det.ast) == (39, 86, 15, 28, 49, 27)
    assert (rows[BOS].is_home, rows[BOS].won, rows[BOS].opponent_pts) == (False, False, 121)


def test_team_rows_come_with_the_player_batch(response: dict, team_response: dict) -> None:
    batch = normalize_league_game_log(response, team_response)
    assert len(batch.team_stats) == 4
    assert batch.cleanups == {}


def test_team_log_with_a_game_the_player_log_lacks_is_an_error(
    response: dict, team_response: dict
) -> None:
    set_team_column(team_response, "GAME_ID", "0022600099", row_index=0)
    set_team_column(team_response, "GAME_ID", "0022600099", row_index=2)
    with pytest.raises(NormalizeError, match="player log doesn't"):
        normalize_league_game_log(response, team_response)


def set_team_column(team_response: dict, column: str, value, row_index: int) -> None:
    set_column(team_response, column, value, row_index)  # same response shape


# Row order in the team fixture: BOS, PHI, DET, NYK.
def test_unrecorded_team_minutes_are_none(team_response: dict) -> None:
    set_team_column(team_response, "MIN", 0, row_index=2)
    assert team_rows(team_response)[DET].minutes is None


def test_neutral_site_team_rows(team_response: dict) -> None:
    set_team_column(team_response, "MATCHUP", "DET @ BOS", row_index=2)
    rows = team_rows(team_response)
    assert rows[DET].is_home is None and rows[BOS].is_home is None


def test_missing_team_result_is_filled_in_from_the_opponent(team_response: dict) -> None:
    set_team_column(team_response, "WL", None, row_index=0)
    cleanups: Counter[str] = Counter()
    rows = {t.team_id: t for t in normalize_team_game_log(team_response, cleanups)}
    assert rows[BOS].won is False
    assert +cleanups == {"win_loss_filled_in": 1}


def test_unplayed_game_is_dropped(team_response: dict) -> None:
    # Real example: the 2012-13 BOS-IND game, cancelled, listed with no W/L and 0-0.
    for i in (0, 2):
        set_team_column(team_response, "WL", None, row_index=i)
        set_team_column(team_response, "PTS", 0, row_index=i)
    cleanups: Counter[str] = Counter()
    rows = normalize_team_game_log(team_response, cleanups)
    assert {t.game_id for t in rows} == {"0022600002"}
    assert +cleanups == {"unplayed_games_dropped": 1}


def test_team_result_must_match_the_score(team_response: dict) -> None:
    set_team_column(team_response, "PTS", 130, row_index=0)  # BOS "lost" 130-121
    with pytest.raises(NormalizeError, match="doesn't match the score"):
        normalize_team_game_log(team_response)


def test_team_log_needs_two_teams_per_game(team_response: dict) -> None:
    team_response["resultSets"][0]["rowSet"].pop(0)
    with pytest.raises(NormalizeError, match="expected 2"):
        normalize_team_game_log(team_response)


def test_team_log_decides_home_and_away(response: dict, team_response: dict) -> None:
    # Real example: 1967-68 SFW-SDR, where the player rows' majority said SDR was home
    # but the team log says SFW.
    set_matchups(response, "0022600002", {PHI: "PHI vs. NYK", NYK: "NYK @ PHI"})

    batch = normalize_league_game_log(response, team_response)

    game = next(g for g in batch.games if g.game_id == "0022600002")
    assert (game.home_team_id, game.away_team_id) == (NYK, PHI)
    game_rows = [s for s in batch.stats if s.game_id == "0022600002"]
    assert all(s.is_home == (s.team_id == NYK) for s in game_rows)
    assert batch.cleanups == {"home_away_outvoted": len(game_rows)}


def test_team_log_decides_the_result(response: dict, team_response: dict) -> None:
    # Real example: 1974-11-19 CLE @ HOU, where the player rows said CLE won 85-94.
    set_wl_for_team(response, "0022600001", BOS, "W")
    set_wl_for_team(response, "0022600001", DET, "L")

    batch = normalize_league_game_log(response, team_response)

    game_rows = [s for s in batch.stats if s.game_id == "0022600001"]
    assert all(s.won == (s.team_id == DET) for s in game_rows)
    assert batch.cleanups == {"win_loss_overruled": len(game_rows)}


def test_games_missing_from_the_team_log_use_the_player_labels(
    response: dict, team_response: dict
) -> None:
    rows = team_response["resultSets"][0]["rowSet"]
    rows[:] = [r for r in rows if "0022600002" not in r]  # only BOS @ DET in the team log
    set_column(response, "MATCHUP", "PHI vs. NYK", row_index=0)  # outvoted by teammates

    batch = normalize_league_game_log(response, team_response)

    game = next(g for g in batch.games if g.game_id == "0022600002")
    assert (game.home_team_id, game.away_team_id) == (NYK, PHI)
    assert len(batch.team_stats) == 2
    assert batch.cleanups == {"home_away_outvoted": 1}


def test_player_team_missing_from_the_team_log_is_an_error(
    response: dict, team_response: dict
) -> None:
    set_team_column(team_response, "TEAM_ID", 1, row_index=0)  # BOS replaced
    with pytest.raises(NormalizeError, match="doesn't list it"):
        normalize_league_game_log(response, team_response)


def as_playoffs(response: dict) -> dict:
    """The same games relabelled as 2026-27 playoff games."""
    for row_index in range(len(response["resultSets"][0]["rowSet"])):
        set_column(response, "SEASON_ID", "42026", row_index)
    return response


def test_playoff_games_get_a_round_and_game_number(response: dict, team_response: dict) -> None:
    batch = normalize_league_game_log(as_playoffs(response), as_playoffs(team_response))

    # Two series under way, each one game in: the first round of playoffs in progress.
    rounds = {(g.playoff_round, g.playoff_round_name, g.playoff_series_game) for g in batch.games}
    assert rounds == {(1, "First Round", 1)}


def test_regular_season_games_have_no_playoff_round(response: dict, team_response: dict) -> None:
    batch = normalize_league_game_log(response, team_response)
    assert all(g.playoff_round is None for g in batch.games)


def test_playoffs_that_arent_a_bracket_are_left_without_rounds(
    response: dict, team_response: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(  # what playoff_rounds says when the games aren't a bracket
        "nba_query_app.ingest.normalize.playoff_rounds", lambda season, rows: None
    )

    batch = normalize_league_game_log(as_playoffs(response), as_playoffs(team_response))

    assert all(g.playoff_round is None for g in batch.games)
    assert batch.cleanups == {"playoff_rounds_not_inferred": 2}
