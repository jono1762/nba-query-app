"""Loading normalized batches into the database, as the nba_ingest role would."""

import dataclasses
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import psycopg
import pytest

from nba_query_app.ingest.load import load_batch, load_cup_rounds
from nba_query_app.ingest.normalize import Batch, normalize_league_game_log

FIXTURE = Path(__file__).parent / "fixtures" / "leaguegamelog_2026-27_two_games.json"
TEAM_FIXTURE = Path(__file__).parent / "fixtures" / "leaguegamelog_2026-27_two_games_team.json"
BOS = 1610612738


@pytest.fixture
def batch() -> Batch:
    return normalize_league_game_log(
        json.loads(FIXTURE.read_text()), json.loads(TEAM_FIXTURE.read_text())
    )


@pytest.fixture
def ingest(connect_as: Callable[[str], psycopg.Connection]) -> psycopg.Connection:
    """Load using the same limited permissions the real loader will have."""
    conn = connect_as("nba_ingest")
    conn.autocommit = False
    return conn


def count(conn: psycopg.Connection, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM nba.{table}").fetchone()[0]


def meta(conn: psycopg.Connection) -> tuple[int, date | None]:
    return conn.execute("SELECT data_version, latest_game_date FROM nba.meta").fetchone()


def test_first_load_inserts_everything(ingest: psycopg.Connection, batch: Batch) -> None:
    result = load_batch(ingest, batch)

    assert (result.teams, result.players, result.games, result.stats) == (4, 41, 2, 41)
    assert count(ingest, "player_game_stats") == 41
    assert count(ingest, "games") == 2
    assert meta(ingest) == (1, date(2026, 10, 20))


def test_loading_the_same_data_twice_changes_nothing(
    ingest: psycopg.Connection, batch: Batch
) -> None:
    load_batch(ingest, batch)
    result = load_batch(ingest, batch)

    assert result.total == 0
    assert count(ingest, "player_game_stats") == 41
    assert meta(ingest) == (1, date(2026, 10, 20))  # version not bumped


def test_corrected_stat_is_updated(ingest: psycopg.Connection, batch: Batch) -> None:
    load_batch(ingest, batch)
    first = batch.stats[0]
    corrected = dataclasses.replace(first, pts=first.pts + 2)

    result = load_batch(ingest, dataclasses.replace(batch, stats=[corrected]))

    assert result.stats == 1
    pts = ingest.execute(
        "SELECT pts FROM nba.player_game_stats WHERE game_id = %s AND player_id = %s",
        (first.game_id, first.player_id),
    ).fetchone()[0]
    assert pts == first.pts + 2
    assert meta(ingest)[0] == 2


def test_older_name_does_not_overwrite_newer(ingest: psycopg.Connection, batch: Batch) -> None:
    load_batch(ingest, batch)
    stale = Batch(
        teams=[
            dataclasses.replace(t, name="Old Name", name_as_of=date(2001, 1, 1))
            for t in batch.teams
        ],
        players=[],
        games=[],
        stats=[],
    )

    assert load_batch(ingest, stale).teams == 0
    name = ingest.execute("SELECT name FROM nba.teams WHERE team_id = %s", (BOS,)).fetchone()[0]
    assert name == "Boston Celtics"


def test_newer_name_does_overwrite(ingest: psycopg.Connection, batch: Batch) -> None:
    load_batch(ingest, batch)
    renamed = Batch(
        teams=[
            dataclasses.replace(t, name="New Name", name_as_of=date(2027, 1, 1))
            for t in batch.teams
        ],
        players=[],
        games=[],
        stats=[],
    )

    assert load_batch(ingest, renamed).teams == 4
    name = ingest.execute("SELECT name FROM nba.teams WHERE team_id = %s", (BOS,)).fetchone()[0]
    assert name == "New Name"


def test_failed_load_saves_nothing(ingest: psycopg.Connection, batch: Batch) -> None:
    broken_row = dataclasses.replace(batch.stats[-1], fgm=99)  # more makes than attempts
    broken = dataclasses.replace(batch, stats=[*batch.stats[:-1], broken_row])

    with pytest.raises(psycopg.errors.CheckViolation):
        load_batch(ingest, broken)

    assert count(ingest, "teams") == 0
    assert count(ingest, "player_game_stats") == 0
    assert meta(ingest) == (0, None)


def test_latest_game_date_never_goes_backwards(ingest: psycopg.Connection, batch: Batch) -> None:
    load_batch(ingest, batch)
    old_game = dataclasses.replace(
        batch.games[0], game_id="0020000001", game_date=date(2000, 11, 1)
    )

    load_batch(ingest, Batch(teams=[], players=[], games=[old_game], stats=[]))

    assert meta(ingest) == (2, date(2026, 10, 20))


def test_neutral_site_game_loads(ingest: psycopg.Connection, batch: Batch) -> None:
    game_id = batch.games[0].game_id
    neutral = dataclasses.replace(
        batch,
        games=[
            dataclasses.replace(g, home_team_id=None, away_team_id=None, neutral_site=True)
            if g.game_id == game_id
            else g
            for g in batch.games
        ],
        stats=[
            dataclasses.replace(s, is_home=None) if s.game_id == game_id else s for s in batch.stats
        ],
    )

    load_batch(ingest, neutral)

    row = ingest.execute(
        "SELECT neutral_site, home_team_id, away_team_id FROM nba.games WHERE game_id = %s",
        (game_id,),
    ).fetchone()
    assert row == (True, None, None)


def test_neutral_site_game_with_a_home_team_is_rejected(
    ingest: psycopg.Connection, batch: Batch
) -> None:
    half_neutral = dataclasses.replace(batch.games[0], neutral_site=True)  # still has home team
    with pytest.raises(psycopg.errors.CheckViolation):
        load_batch(ingest, dataclasses.replace(batch, games=[half_neutral], stats=[]))


def test_player_can_have_rows_for_both_teams_in_one_game(
    ingest: psycopg.Connection, batch: Batch
) -> None:
    # Eric Money, 1978-11-08: genuine stats for both teams in one (protested) game.
    first = batch.stats[0]
    other_team = dataclasses.replace(
        first, team_id=first.opponent_team_id, opponent_team_id=first.team_id, pts=4
    )
    load_batch(ingest, dataclasses.replace(batch, stats=[*batch.stats, other_team]))

    count = ingest.execute(
        "SELECT count(*) FROM nba.player_game_stats WHERE game_id = %s AND player_id = %s",
        (first.game_id, first.player_id),
    ).fetchone()[0]
    assert count == 2


def test_cup_rounds_tag_stored_games(ingest: psycopg.Connection, batch: Batch) -> None:
    load_batch(ingest, batch)
    rounds = {batch.games[0].game_id: "Group", batch.games[1].game_id: "Quarterfinal"}

    assert load_cup_rounds(ingest, rounds) == 2
    stored = dict(ingest.execute("SELECT game_id, nba_cup_round FROM nba.games").fetchall())
    assert stored == rounds
    assert meta(ingest)[0] == 2  # data version bumped once for the change


def test_cup_rounds_twice_change_nothing(ingest: psycopg.Connection, batch: Batch) -> None:
    load_batch(ingest, batch)
    rounds = {batch.games[0].game_id: "Group"}
    load_cup_rounds(ingest, rounds)

    assert load_cup_rounds(ingest, rounds) == 0
    assert meta(ingest)[0] == 2


def test_cup_rounds_for_games_not_loaded_is_an_error(ingest: psycopg.Connection) -> None:
    with pytest.raises(ValueError, match="aren't loaded yet"):
        load_cup_rounds(ingest, {"0022400999": "Group"})


def test_reloading_a_cup_game_keeps_its_round(ingest: psycopg.Connection, batch: Batch) -> None:
    # The round isn't part of the Game dataclass, so a regular-season reload leaves it alone.
    load_batch(ingest, batch)
    load_cup_rounds(ingest, {batch.games[0].game_id: "Semifinal"})
    corrected = dataclasses.replace(batch.stats[0], pts=batch.stats[0].pts + 1)
    load_batch(ingest, dataclasses.replace(batch, stats=[corrected]))

    round_ = ingest.execute(
        "SELECT nba_cup_round FROM nba.games WHERE game_id = %s", (batch.games[0].game_id,)
    ).fetchone()[0]
    assert round_ == "Semifinal"


def test_team_box_scores_are_loaded_once(ingest: psycopg.Connection) -> None:
    batch = normalize_league_game_log(
        json.loads(FIXTURE.read_text()), json.loads(TEAM_FIXTURE.read_text())
    )

    assert load_batch(ingest, batch).team_stats == 4
    assert load_batch(ingest, batch).total == 0  # reloading changes nothing
    scores = ingest.execute(
        "SELECT team_abbreviation, pts, opponent_pts, won FROM nba.team_game_stats "
        "WHERE game_id = '0022600001' ORDER BY pts"
    ).fetchall()
    assert scores == [("BOS", 113, 121, False), ("DET", 121, 113, True)]
