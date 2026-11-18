# /// script
# requires-python = ">=3.12"
# dependencies = ["nba_api>=1.10"]
# ///
"""Spike: can we pull a whole season of player game logs from stats.nba.com in one call?

This is the "history" source. stats.nba.com blocks cloud servers, so this is meant
to run on a home internet connection.

Usage:
    uv run spikes/stats_nba_gamelog.py              # current season
    uv run spikes/stats_nba_gamelog.py 2024-25      # a specific season

Saves the raw response to spikes/output/ so other spikes can compare against it.
"""

import json
import sys
import time
from datetime import date
from pathlib import Path

from nba_api.stats.endpoints import leaguegamelog

OUTPUT_DIR = Path(__file__).parent / "output"


def current_season(today: date) -> str:
    """NBA seasons start in October, e.g. Nov 2026 -> '2026-27', Mar 2027 -> '2026-27'."""
    start_year = today.year if today.month >= 10 else today.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def main() -> None:
    season = sys.argv[1] if len(sys.argv) > 1 else current_season(date.today())
    print(f"Fetching player game logs for {season} (regular season)...")

    started = time.perf_counter()
    endpoint = leaguegamelog.LeagueGameLog(
        season=season,
        season_type_all_star="Regular Season",
        player_or_team_abbreviation="P",  # P = one row per player per game
        timeout=60,
    )
    elapsed = time.perf_counter() - started

    result = endpoint.get_dict()["resultSets"][0]
    headers, rows = result["headers"], result["rowSet"]

    print(f"Took {elapsed:.1f}s")
    print(f"Rows: {len(rows)}")
    print(f"Distinct games: {len({row[headers.index('GAME_ID')] for row in rows})}")
    print(f"Columns ({len(headers)}): {', '.join(headers)}")
    if rows:
        print("Sample row:")
        for header, value in zip(headers, rows[0]):
            print(f"  {header:<18} {value!r}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_file = OUTPUT_DIR / f"stats_gamelog_{season}.json"
    out_file.write_text(json.dumps({"headers": headers, "rows": rows}))
    print(f"Saved to {out_file}")


if __name__ == "__main__":
    main()
