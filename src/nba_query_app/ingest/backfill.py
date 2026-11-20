"""Download seasons from stats.nba.com and load them into the database.

Used for the one-time history download and, later, the daily update (which is
just this command for the current season).

Each season and season type is fetched, converted and saved on its own, in one
transaction. If the run stops partway, run it again: seasons already saved are
re-checked quickly and unchanged rows are left alone.

Usage:
    uv run --env-file .env python -m nba_query_app.ingest.backfill                # 2000-01 to now
    uv run --env-file .env python -m nba_query_app.ingest.backfill --from 2026-27 # current season
    uv run --env-file .env python -m nba_query_app.ingest.backfill --season-type playoffs
"""

import argparse
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg

from nba_query_app.ingest.fetch import SEASON_TYPES, fetch_league_game_log
from nba_query_app.ingest.load import LoadResult, load_batch
from nba_query_app.ingest.normalize import normalize_league_game_log
from nba_query_app.seasons import season_for_date, season_start_year, seasons_between

FIRST_SEASON = "2000-01"
SEASON_TYPE_CHOICES = {
    "regular": ("Regular Season",),
    "playoffs": ("Playoffs",),
    "both": SEASON_TYPES,
}


@dataclass(frozen=True)
class SeasonResult:
    season: str
    season_type: str
    rows_fetched: int
    loaded: LoadResult


def backfill(
    conn: psycopg.Connection,
    seasons: Sequence[str],
    season_types: Sequence[str],
    *,
    fetch: Callable[[str, str], dict[str, Any]] = fetch_league_game_log,
    pause_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> list[SeasonResult]:
    """Fetch, convert and load each season/type in turn, pausing between requests."""
    results = []
    jobs = [(season, season_type) for season in seasons for season_type in season_types]
    for index, (season, season_type) in enumerate(jobs):
        if index:
            sleep(pause_seconds)  # be polite to stats.nba.com
        started = time.perf_counter()
        response = fetch(season, season_type)
        batch = normalize_league_game_log(response)
        loaded = load_batch(conn, batch)
        result = SeasonResult(season, season_type, len(batch.stats), loaded)
        results.append(result)
        log(
            f"{season} {season_type:<14} {result.rows_fetched:>6} rows, "
            f"{loaded.stats:>6} new/changed  ({time.perf_counter() - started:.1f}s)"
        )
    return results


def parse_args(argv: Sequence[str] | None, today: date) -> argparse.Namespace:
    current = season_for_date(today)
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--from", dest="first", default=FIRST_SEASON, help="first season, e.g. 2000-01"
    )
    parser.add_argument(
        "--to", dest="last", default=current, help=f"last season (default {current})"
    )
    parser.add_argument("--season-type", choices=SEASON_TYPE_CHOICES, default="both")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between requests")
    args = parser.parse_args(argv)

    if season_start_year(args.last) > season_start_year(current):
        parser.error(f"--to {args.last} is in the future (current season is {current})")
    args.seasons = seasons_between(args.first, args.last)
    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv, date.today())
    season_types = SEASON_TYPE_CHOICES[args.season_type]
    print(f"Loading {len(args.seasons)} season(s) x {len(season_types)} type(s)...")

    started = time.perf_counter()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        results = backfill(conn, args.seasons, season_types, pause_seconds=args.pause)
        version, latest = conn.execute(
            "SELECT data_version, latest_game_date FROM nba.meta"
        ).fetchone()

    total_rows = sum(r.rows_fetched for r in results)
    total_changed = sum(r.loaded.stats for r in results)
    print(
        f"Done in {time.perf_counter() - started:.0f}s: {total_rows} rows, "
        f"{total_changed} new/changed. Data version {version}, latest game {latest}."
    )


if __name__ == "__main__":
    main()
