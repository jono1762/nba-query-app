"""The backfill command, with stats.nba.com faked out."""

import json
from datetime import date
from pathlib import Path

import psycopg
import pytest

from nba_query_app.db.migrate import apply_migrations
from nba_query_app.ingest.backfill import backfill, parse_args

FIXTURE = Path(__file__).parent / "fixtures" / "leaguegamelog_2026-27_two_games.json"
TEAM_FIXTURE = Path(__file__).parent / "fixtures" / "leaguegamelog_2026-27_two_games_team.json"
TODAY = date(2026, 11, 20)


def empty_response() -> dict:
    headers = json.loads(FIXTURE.read_text())["resultSets"][0]["headers"]
    return {"resultSets": [{"headers": headers, "rowSet": []}]}


class FakeFetch:
    """Two real games for 2026-27 regular season; no games for anything else."""

    def __init__(self):
        self.calls = []

    def __call__(self, season, season_type, level):
        self.calls.append((season, season_type, level))
        if (season, season_type) == ("2026-27", "Regular Season"):
            return json.loads((FIXTURE if level == "player" else TEAM_FIXTURE).read_text())
        return empty_response()


@pytest.fixture
def migrated(db: psycopg.Connection) -> psycopg.Connection:
    apply_migrations(db)
    return db


def run(conn, seasons, season_types, fetch, sleeps=None, logs=None):
    return backfill(
        conn,
        seasons,
        season_types,
        fetch=fetch,
        pause_seconds=1.5,
        sleep=(sleeps if sleeps is not None else []).append,
        log=(logs if logs is not None else []).append,
    )


def test_fetches_every_season_and_type_and_loads_rows(migrated: psycopg.Connection) -> None:
    fetch, sleeps, logs = FakeFetch(), [], []

    results = run(
        migrated, ["2025-26", "2026-27"], ["Regular Season", "Playoffs"], fetch, sleeps, logs
    )

    assert fetch.calls == [
        (season, season_type, level)
        for season in ("2025-26", "2026-27")
        for season_type in ("Regular Season", "Playoffs")
        for level in ("player", "team")
    ]
    assert sleeps == [1.5] * 7  # between requests, not before the first
    assert [r.rows_fetched for r in results] == [0, 0, 41, 0]
    assert migrated.execute("SELECT count(*) FROM nba.player_game_stats").fetchone() == (41,)
    assert migrated.execute("SELECT count(*) FROM nba.team_game_stats").fetchone() == (4,)
    assert len(logs) == 4 and "2026-27 Regular Season" in logs[2]


def test_cleanup_counts_are_reported(migrated: psycopg.Connection) -> None:
    def fetch_with_missing_win_loss(season, season_type, level):
        if level == "team":
            return json.loads(TEAM_FIXTURE.read_text())
        response = json.loads(FIXTURE.read_text())
        headers = response["resultSets"][0]["headers"]
        response["resultSets"][0]["rowSet"][0][headers.index("WL")] = None
        return response

    logs = []
    results = run(migrated, ["2026-27"], ["Regular Season"], fetch_with_missing_win_loss, logs=logs)

    assert results[0].cleanups == {"win_loss_filled_in": 1}
    assert "win_loss_filled_in=1" in logs[0]


def test_running_again_changes_nothing(migrated: psycopg.Connection) -> None:
    run(migrated, ["2026-27"], ["Regular Season"], FakeFetch())
    results = run(migrated, ["2026-27"], ["Regular Season"], FakeFetch())

    assert results[0].loaded.total == 0
    assert migrated.execute("SELECT data_version FROM nba.meta").fetchone() == (1,)


def test_failure_keeps_seasons_already_saved(migrated: psycopg.Connection) -> None:
    def fetch(season, season_type, level):
        if season == "2027-28":
            raise RuntimeError("network down")
        return FakeFetch()(season, season_type, level)

    with pytest.raises(RuntimeError):
        run(migrated, ["2026-27", "2027-28"], ["Regular Season"], fetch)

    assert migrated.execute("SELECT count(*) FROM nba.player_game_stats").fetchone() == (41,)


def test_args_default_to_full_history_all_types() -> None:
    args = parse_args([], TODAY)
    assert args.seasons[0] == "1946-47"
    assert args.seasons[-1] == "2026-27"
    assert len(args.seasons) == 81
    assert args.season_type == "all"


def test_args_single_season() -> None:
    args = parse_args(["--from", "2026-27", "--season-type", "regular"], TODAY)
    assert args.seasons == ["2026-27"]


def test_args_reject_future_season() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--to", "2027-28"], TODAY)


def test_season_types_only_requested_once_they_existed(migrated: psycopg.Connection) -> None:
    fetch = FakeFetch()
    all_types = ["Regular Season", "NBA Cup Final", "Play-In", "Playoffs"]
    run(migrated, ["2018-19", "2019-20", "2023-24"], all_types, fetch)

    assert [call[:2] for call in fetch.calls if call[2] == "player"] == [
        ("2018-19", "Regular Season"),
        ("2018-19", "Playoffs"),
        ("2019-20", "Regular Season"),  # play-in from 2019-20
        ("2019-20", "Play-In"),
        ("2019-20", "Playoffs"),
        ("2023-24", "Regular Season"),  # NBA Cup final from 2023-24
        ("2023-24", "NBA Cup Final"),
        ("2023-24", "Play-In"),
        ("2023-24", "Playoffs"),
    ]


def test_cup_request_tags_regular_season_games_with_their_round(
    migrated: psycopg.Connection,
) -> None:
    # The Cup ("IST") response repeats regular-season games; here, the fixture's two games.
    def fetch(season, season_type, level):
        if season_type in ("Regular Season", "NBA Cup Final"):
            return json.loads((FIXTURE if level == "player" else TEAM_FIXTURE).read_text())
        return empty_response()

    run(migrated, ["2026-27"], ["Regular Season", "NBA Cup Final"], fetch)

    rounds = migrated.execute("SELECT game_id, nba_cup_round FROM nba.games ORDER BY 1").fetchall()
    assert rounds == [("0022600001", "Group"), ("0022600002", "Group")]
    # Only the final's rows are loaded as box scores, so nothing was added twice.
    assert migrated.execute("SELECT count(*) FROM nba.player_game_stats").fetchone() == (41,)
