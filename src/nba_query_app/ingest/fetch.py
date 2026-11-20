"""Download player game logs from stats.nba.com.

stats.nba.com blocks cloud servers, so this only works from a home connection.
It is also occasionally slow or flaky, so failed requests are retried with
increasing waits (5s, 10s, ...) before giving up.
"""

import time
from collections.abc import Callable
from typing import Any

import requests
from nba_api.stats.endpoints import leaguegamelog

# Official, competitive games only; exhibitions (All-Star, preseason) are excluded.
# The NBA Cup final exists from 2023-24, play-in from 2019-20.
SEASON_TYPES = ("Regular Season", "NBA Cup Final", "Play-In", "Playoffs")
# stats.nba.com's names. "IST" (in-season tournament) returns every Cup game, but
# only the final is its own season type (SEASON_ID prefix 6); the rest are
# regular-season games we already get, so they're filtered out.
API_SEASON_TYPE = {
    "Regular Season": "Regular Season",
    "NBA Cup Final": "IST",
    "Play-In": "PlayIn",
    "Playoffs": "Playoffs",
}
CUP_FINAL_SEASON_ID_PREFIX = "6"


class FetchError(RuntimeError):
    """stats.nba.com didn't return usable data after all retries."""


def _request_league_game_log(season: str, season_type: str, timeout: float) -> dict[str, Any]:
    endpoint = leaguegamelog.LeagueGameLog(
        season=season,
        season_type_all_star=API_SEASON_TYPE[season_type],
        player_or_team_abbreviation="P",  # one row per player per game
        timeout=timeout,
    )
    return endpoint.get_dict()


def fetch_league_game_log(
    season: str,
    season_type: str,
    *,
    attempts: int = 3,
    backoff_seconds: float = 5.0,
    timeout: float = 60.0,
    request: Callable[[str, str, float], dict[str, Any]] = _request_league_game_log,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Return the raw LeagueGameLog response for one season and season type.

    `request` and `sleep` can be swapped out in tests so nothing hits the network.
    """
    if season_type not in SEASON_TYPES:
        raise ValueError(f"season_type must be one of {SEASON_TYPES}, got {season_type!r}")

    for attempt in range(1, attempts + 1):
        try:
            response = request(season, season_type, timeout)
            result = response["resultSets"][0]
            result["rowSet"]  # noqa: B018 - check the shape we rely on
            if season_type == "NBA Cup Final":
                season_id = result["headers"].index("SEASON_ID")
                result["rowSet"] = [
                    row
                    for row in result["rowSet"]
                    if row[season_id].startswith(CUP_FINAL_SEASON_ID_PREFIX)
                ]
            return response
        # RequestException: network/timeout. ValueError: body wasn't JSON (often a block page).
        # KeyError/IndexError/TypeError: JSON, but not the shape we expect.
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as error:
            if attempt == attempts:
                raise FetchError(
                    f"Giving up on {season} {season_type} after {attempts} attempts: {error!r}"
                ) from error
            sleep(backoff_seconds * attempt)

    raise AssertionError("unreachable")
