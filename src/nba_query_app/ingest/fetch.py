"""Download from stats.nba.com: game logs (one row per player per game, or per team
per game) and player details (the player index, and one player's info).

stats.nba.com blocks cloud servers, so this only works from a home connection.
It is also occasionally slow or flaky, so failed requests are retried with
increasing waits (5s, 10s, ...) before giving up.
"""

import time
from collections.abc import Callable
from typing import Any

import requests
from nba_api.stats.endpoints import commonplayerinfo, leaguegamelog, playerindex

# Official, competitive games only; exhibitions (All-Star, preseason) are excluded.
# The NBA Cup final exists from 2023-24, play-in from 2019-20.
SEASON_TYPES = ("Regular Season", "NBA Cup Final", "Play-In", "Playoffs")
# stats.nba.com's names. "IST" (in-season tournament) returns every Cup game, not
# just the final; ingest/cup.py splits that response (see backfill.py).
API_SEASON_TYPE = {
    "Regular Season": "Regular Season",
    "NBA Cup Final": "IST",
    "Play-In": "PlayIn",
    "Playoffs": "Playoffs",
}


# "player": one row per player per game (box score lines).
# "team": one row per team per game (final score and team totals).
LEVELS = {"player": "P", "team": "T"}


class FetchError(RuntimeError):
    """stats.nba.com didn't return usable data after all retries."""


def _with_retries(
    call: Callable[[], dict[str, Any]],
    what: str,
    attempts: int,
    backoff_seconds: float,
    sleep: Callable[[float], None],
    empty_means_no_record: bool = False,
) -> dict[str, Any]:
    """Make the request, retrying with increasing waits; check the shape we rely on.

    With empty_means_no_record, an empty response ({}) is a valid answer ("the source
    has no record"); otherwise it counts as a failure and is retried.
    """
    for attempt in range(1, attempts + 1):
        try:
            response = call()
            if empty_means_no_record and response == {}:
                return response
            response["resultSets"][0]["rowSet"]  # noqa: B018 - check the shape we rely on
            return response
        # RequestException: network/timeout. ValueError: body wasn't JSON (often a block page).
        # KeyError/IndexError/TypeError: JSON, but not the shape we expect.
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as error:
            if attempt == attempts:
                raise FetchError(
                    f"Giving up on {what} after {attempts} attempts: {error!r}"
                ) from error
            sleep(backoff_seconds * attempt)
    raise AssertionError("unreachable")


def _request_league_game_log(
    season: str, season_type: str, level: str, timeout: float
) -> dict[str, Any]:
    endpoint = leaguegamelog.LeagueGameLog(
        season=season,
        season_type_all_star=API_SEASON_TYPE[season_type],
        player_or_team_abbreviation=LEVELS[level],
        timeout=timeout,
    )
    return endpoint.get_dict()


def fetch_league_game_log(
    season: str,
    season_type: str,
    level: str = "player",
    *,
    attempts: int = 3,
    backoff_seconds: float = 5.0,
    timeout: float = 60.0,
    request: Callable[[str, str, str, float], dict[str, Any]] = _request_league_game_log,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Return the raw LeagueGameLog response for one season, season type and level.

    `request` and `sleep` can be swapped out in tests so nothing hits the network.
    """
    if season_type not in SEASON_TYPES:
        raise ValueError(f"season_type must be one of {SEASON_TYPES}, got {season_type!r}")
    if level not in LEVELS:
        raise ValueError(f"level must be one of {tuple(LEVELS)}, got {level!r}")

    return _with_retries(
        lambda: request(season, season_type, level, timeout),
        f"{season} {season_type} ({level} log)",
        attempts,
        backoff_seconds,
        sleep,
    )


def _request_player_index(timeout: float) -> dict[str, Any]:
    # Any current season works: "historical" includes every player who ever played.
    endpoint = playerindex.PlayerIndex(historical_nullable="1", timeout=timeout)
    return endpoint.get_dict()


def fetch_player_index(
    *,
    attempts: int = 3,
    backoff_seconds: float = 5.0,
    timeout: float = 60.0,
    request: Callable[[float], dict[str, Any]] = _request_player_index,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Every player in history, one row each: position, height, weight, college,
    country and draft. One request. No birth dates (see fetch_player_info)."""
    return _with_retries(
        lambda: request(timeout), "the player index", attempts, backoff_seconds, sleep
    )


def _request_player_info(player_id: int, timeout: float) -> dict[str, Any]:
    endpoint = commonplayerinfo.CommonPlayerInfo(
        player_id=player_id, timeout=timeout, get_request=False
    )
    try:
        endpoint.get_request()
    except KeyError:
        # nba_api fails to parse the empty body the source sends for an unknown player.
        if endpoint.nba_response.get_dict() == {}:
            return {}
        raise
    return endpoint.get_dict()


def fetch_player_info(
    player_id: int,
    *,
    attempts: int = 3,
    backoff_seconds: float = 5.0,
    timeout: float = 60.0,
    request: Callable[[int, float], dict[str, Any]] = _request_player_info,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any] | None:
    """One player's info, including birth date. One request per player.

    Returns None if the source has no record of the player (it sends an empty body).
    """
    response = _with_retries(
        lambda: request(player_id, timeout),
        f"player {player_id}'s info",
        attempts,
        backoff_seconds,
        sleep,
        empty_means_no_record=True,
    )
    return response or None
