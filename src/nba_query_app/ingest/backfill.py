"""Download seasons from stats.nba.com and load them into the database.

Used for the one-time history download and, later, the daily update (which is
just this command for the current season).

Each season and season type is fetched (the player-level and the team-level log:
two requests), converted and saved on its own, in one transaction. If the run
stops partway, run it again: seasons already saved are re-checked quickly and
unchanged rows are left alone. At the end, the data-quality
checks (nba_query_app.quality) run over the whole database and print a report.

Usage:
    uv run --env-file .env python -m nba_query_app.ingest.backfill                # 1946-47 to now
    uv run --env-file .env python -m nba_query_app.ingest.backfill --from 2026-27 # current season
    uv run --env-file .env python -m nba_query_app.ingest.backfill --season-type playoffs
"""

import argparse
import os
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg

from nba_query_app.ingest.cup import cup_rounds, final_only
from nba_query_app.ingest.fetch import SEASON_TYPES, fetch_league_game_log
from nba_query_app.ingest.load import LoadResult, load_batch, load_cup_rounds
from nba_query_app.ingest.normalize import CLEANUP_RULES, normalize_league_game_log
from nba_query_app.quality import report, run_checks
from nba_query_app.seasons import season_for_date, season_start_year, seasons_between

FIRST_SEASON = "1946-47"  # the first BAA season; the BAA became the NBA in 1949
PLAY_IN_FIRST_SEASON = "2019-20"
CUP_FINAL_FIRST_SEASON = "2023-24"
SEASON_TYPE_CHOICES = {
    "regular": ("Regular Season",),
    "cup-final": ("NBA Cup Final",),
    "play-in": ("Play-In",),
    "playoffs": ("Playoffs",),
    "all": SEASON_TYPES,
}


@dataclass(frozen=True)
class SeasonResult:
    season: str
    season_type: str
    rows_fetched: int
    loaded: LoadResult
    cleanups: Counter[str]  # how often each normalize cleanup rule fired


def backfill(
    conn: psycopg.Connection,
    seasons: Sequence[str],
    season_types: Sequence[str],
    *,
    fetch: Callable[[str, str, str], dict[str, Any]] = fetch_league_game_log,
    pause_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> list[SeasonResult]:
    """Fetch, convert and load each season/type in turn, pausing between requests.

    Season types are skipped for seasons before they existed (play-in 2019-20,
    NBA Cup final 2023-24). The NBA Cup request returns every Cup game: the final's
    box scores are loaded, and every Cup game is tagged with its round. That relies
    on the regular season (which holds the other Cup games) being loaded first,
    which the order of SEASON_TYPES guarantees within each season.
    """
    results = []
    jobs = [
        (season, season_type)
        for season in seasons
        for season_type in season_types
        if season_type_existed(season, season_type)
    ]
    for index, (season, season_type) in enumerate(jobs):
        if index:
            sleep(pause_seconds)  # be polite to stats.nba.com
        started = time.perf_counter()
        response = fetch(season, season_type, "player")
        sleep(pause_seconds)
        team_response = fetch(season, season_type, "team")
        rounds = {}
        if season_type == "NBA Cup Final":
            rounds = cup_rounds(response)
            response, team_response = final_only(response), final_only(team_response)
        batch = normalize_league_game_log(response, team_response)
        loaded = load_batch(conn, batch)
        rounds_changed = load_cup_rounds(conn, rounds)
        result = SeasonResult(season, season_type, len(batch.stats), loaded, batch.cleanups)
        results.append(result)
        cup_note = f", {len(rounds)} Cup games tagged ({rounds_changed} changed)" if rounds else ""
        cleanup_note = "".join(f", {name}={n}" for name, n in sorted(batch.cleanups.items()))
        log(
            f"{season} {season_type:<14} {result.rows_fetched:>6} rows, "
            f"{loaded.stats:>6} new/changed, {len(batch.team_stats)} team rows "
            f"({loaded.team_stats} new/changed){cup_note}{cleanup_note}  "
            f"({time.perf_counter() - started:.1f}s)"
        )
    return results


def season_type_existed(season: str, season_type: str) -> bool:
    first = {"Play-In": PLAY_IN_FIRST_SEASON, "NBA Cup Final": CUP_FINAL_FIRST_SEASON}
    if season_type not in first:
        return True
    return season_start_year(season) >= season_start_year(first[season_type])


def parse_args(argv: Sequence[str] | None, today: date) -> argparse.Namespace:
    current = season_for_date(today)
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--from", dest="first", default=FIRST_SEASON, help=f"first season (default {FIRST_SEASON})"
    )
    parser.add_argument(
        "--to", dest="last", default=current, help=f"last season (default {current})"
    )
    parser.add_argument("--season-type", choices=SEASON_TYPE_CHOICES, default="all")
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
        quality = run_checks(conn, season_for_date(date.today()))

    total_rows = sum(r.rows_fetched for r in results)
    total_changed = sum(r.loaded.stats for r in results)
    print(
        f"Done in {time.perf_counter() - started:.0f}s: {total_rows} rows, "
        f"{total_changed} new/changed. Data version {version}, latest game {latest}."
    )
    cleanups = sum((r.cleanups for r in results), Counter())
    if cleanups:
        print("Cleanup rules fired:")
        for name, description in CLEANUP_RULES.items():
            print(f"  {cleanups[name]:>6}  {description}")
    report(quality)  # reports only; problems never undo the load


if __name__ == "__main__":
    main()
