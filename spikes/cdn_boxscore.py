"""Spike: can we get finished games and their box scores from cdn.nba.com?

This is the planned "daily update" source. It uses only the Python standard
library, so the same file can later be pasted into an AWS Lambda to test whether
cdn.nba.com works from the cloud.

Usage:
    python3 spikes/cdn_boxscore.py              # most recent finished game
    python3 spikes/cdn_boxscore.py 0022600123   # a specific game ID

Saves the raw box score to spikes/output/ (when running locally).
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
BOXSCORE_URL = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"
GAME_STATUS_FINAL = 3
OUTPUT_DIR = Path(__file__).parent / "output"


def fetch_json(url: str) -> dict:
    # A browser-like User-Agent; some NBA endpoints reject the default Python one.
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def latest_final_game_id(schedule: dict) -> str:
    """Return the ID of the most recent game marked final in the season schedule."""
    now = datetime.now(timezone.utc)
    finished = [
        game
        for game_date in schedule["leagueSchedule"]["gameDates"]
        for game in game_date["games"]
        if game["gameStatus"] == GAME_STATUS_FINAL
        and datetime.fromisoformat(game["gameDateTimeUTC"].replace("Z", "+00:00")) < now
    ]
    if not finished:
        raise SystemExit("No finished games found in the schedule.")
    latest = max(finished, key=lambda game: game["gameDateTimeUTC"])
    return latest["gameId"]


def summarize_boxscore(boxscore: dict) -> None:
    game = boxscore["game"]
    home, away = game["homeTeam"], game["awayTeam"]
    print(f"Game {game['gameId']}: {away['teamTricode']} {away['score']} @ "
          f"{home['teamTricode']} {home['score']}  ({game['gameTimeUTC']})")

    players = home["players"] + away["players"]
    played = [player for player in players if player.get("played") == "1"]
    print(f"Players listed: {len(players)}, actually played: {len(played)}")

    if played:
        sample = played[0]
        print(f"Sample player: {sample['name']} (personId {sample['personId']})")
        print(f"  Stat fields: {', '.join(sample['statistics'].keys())}")
        print(f"  minutes={sample['statistics']['minutes']!r} "
              f"points={sample['statistics']['points']!r}")


def main(game_id: str | None = None) -> dict:
    print("Fetching season schedule...")
    schedule = fetch_json(SCHEDULE_URL)
    print(f"Schedule season: {schedule['leagueSchedule']['seasonYear']}")

    game_id = game_id or latest_final_game_id(schedule)
    print(f"Fetching box score for {game_id}...")
    boxscore = fetch_json(BOXSCORE_URL.format(game_id=game_id))
    summarize_boxscore(boxscore)
    return boxscore


def lambda_handler(event, context):
    """Entry point when pasted into AWS Lambda (Phase 0 cloud test)."""
    boxscore = main()
    return {"ok": True, "gameId": boxscore["game"]["gameId"]}


if __name__ == "__main__":
    result = main(sys.argv[1] if len(sys.argv) > 1 else None)
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_file = OUTPUT_DIR / f"cdn_boxscore_{result['game']['gameId']}.json"
    out_file.write_text(json.dumps(result))
    print(f"Saved to {out_file}")
