"""The backfill command, with stats.nba.com faked out."""

import json
from datetime import date
from pathlib import Path

import psycopg
import pytest

from nba_query_app.db.migrate import apply_migrations
from nba_query_app.ingest.backfill import backfill, parse_args

FIXTURE = Path(__file__).parent / "fixtures" / "leaguegamelog_2026-27_two_games.json"
TODAY = date(2026, 11, 20)


def empty_response() -> dict:
    headers = json.loads(FIXTURE.read_text())["resultSets"][0]["headers"]
    return {"resultSets": [{"headers": headers, "rowSet": []}]}


class FakeFetch:
    """Two real games for 2026-27 regular season; no games for anything else."""

    def __init__(self):
        self.calls = []

    def __call__(self, season, season_type):
        self.calls.append((season, season_type))
        if (season, season_type) == ("2026-27", "Regular Season"):
            return json.loads(FIXTURE.read_text())
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
        ("2025-26", "Regular Season"),
        ("2025-26", "Playoffs"),
        ("2026-27", "Regular Season"),
        ("2026-27", "Playoffs"),
    ]
    assert sleeps == [1.5, 1.5, 1.5]  # between requests, not before the first
    assert [r.rows_fetched for r in results] == [0, 0, 41, 0]
    assert migrated.execute("SELECT count(*) FROM nba.player_game_stats").fetchone() == (41,)
    assert len(logs) == 4 and "2026-27 Regular Season" in logs[2]


def test_running_again_changes_nothing(migrated: psycopg.Connection) -> None:
    run(migrated, ["2026-27"], ["Regular Season"], FakeFetch())
    results = run(migrated, ["2026-27"], ["Regular Season"], FakeFetch())

    assert results[0].loaded.total == 0
    assert migrated.execute("SELECT data_version FROM nba.meta").fetchone() == (1,)


def test_failure_keeps_seasons_already_saved(migrated: psycopg.Connection) -> None:
    def fetch(season, season_type):
        if season == "2027-28":
            raise RuntimeError("network down")
        return FakeFetch()(season, season_type)

    with pytest.raises(RuntimeError):
        run(migrated, ["2026-27", "2027-28"], ["Regular Season"], fetch)

    assert migrated.execute("SELECT count(*) FROM nba.player_game_stats").fetchone() == (41,)


def test_args_default_to_full_history_both_types() -> None:
    args = parse_args([], TODAY)
    assert args.seasons[0] == "2000-01"
    assert args.seasons[-1] == "2026-27"
    assert len(args.seasons) == 27
    assert args.season_type == "both"


def test_args_single_season() -> None:
    args = parse_args(["--from", "2026-27", "--season-type", "regular"], TODAY)
    assert args.seasons == ["2026-27"]


def test_args_reject_future_season() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--to", "2027-28"], TODAY)
