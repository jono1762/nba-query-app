"""Retry behaviour of the stats.nba.com downloader. No network: requests are faked."""

import pytest
import requests

from nba_query_app.ingest.fetch import (
    FetchError,
    fetch_league_game_log,
    fetch_player_index,
    fetch_player_info,
)

GOOD = {"resultSets": [{"headers": ["GAME_ID"], "rowSet": [["0022600001"]]}]}


class FakeRequests:
    """Returns (or raises) the given outcomes in order, recording each call."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, season, season_type, level, timeout):
        self.calls.append((season, season_type, level))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_returns_response_on_first_success() -> None:
    fake, sleeps = FakeRequests(GOOD), []
    assert fetch_league_game_log("2026-27", "Playoffs", request=fake, sleep=sleeps.append) == GOOD
    assert fake.calls == [("2026-27", "Playoffs", "player")]
    assert sleeps == []


def test_retries_with_increasing_waits_then_succeeds() -> None:
    fake = FakeRequests(requests.Timeout(), ValueError("not JSON"), GOOD)
    sleeps = []

    result = fetch_league_game_log("2026-27", "Regular Season", request=fake, sleep=sleeps.append)

    assert result == GOOD
    assert len(fake.calls) == 3
    assert sleeps == [5.0, 10.0]


def test_unexpected_shape_counts_as_a_failure() -> None:
    fake = FakeRequests({"resultSets": []}, GOOD)
    assert fetch_league_game_log("2026-27", "Regular Season", request=fake, sleep=lambda s: None)
    assert len(fake.calls) == 2


def test_gives_up_after_all_attempts() -> None:
    fake = FakeRequests(*[requests.ConnectionError("blocked")] * 3)

    with pytest.raises(FetchError, match=r"Regular Season \(player log\) after 3"):
        fetch_league_game_log("2026-27", "Regular Season", request=fake, sleep=lambda s: None)
    assert len(fake.calls) == 3


def test_rejects_unknown_season_type() -> None:
    with pytest.raises(ValueError):
        fetch_league_game_log("2026-27", "Preseason", request=FakeRequests(), sleep=lambda s: None)


@pytest.fixture
def endpoint_args(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replaces the nba_api endpoint with a fake; returns the arguments it was called with."""
    seen: dict = {}

    class FakeEndpoint:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def get_dict(self):
            return GOOD

    monkeypatch.setattr("nba_query_app.ingest.fetch.leaguegamelog.LeagueGameLog", FakeEndpoint)
    return seen


def test_play_in_uses_the_api_spelling(endpoint_args: dict) -> None:
    fetch_league_game_log("2025-26", "Play-In", sleep=lambda s: None)

    assert endpoint_args["season_type_all_star"] == "PlayIn"
    assert endpoint_args["player_or_team_abbreviation"] == "P"


def test_team_level_asks_for_one_row_per_team(endpoint_args: dict) -> None:
    fetch_league_game_log("2025-26", "Regular Season", "team", sleep=lambda s: None)

    assert endpoint_args["player_or_team_abbreviation"] == "T"


def test_rejects_unknown_level() -> None:
    with pytest.raises(ValueError, match="level"):
        fetch_league_game_log("2026-27", "Playoffs", "coach", request=FakeRequests())


def test_empty_game_log_is_retried_like_any_failure() -> None:
    fake = FakeRequests({}, GOOD)
    assert fetch_league_game_log("2026-27", "Playoffs", request=fake, sleep=lambda s: None) == GOOD


def test_player_index_is_one_request() -> None:
    calls = []

    def fake(timeout):
        calls.append(timeout)
        return GOOD

    assert fetch_player_index(request=fake, sleep=lambda s: None) == GOOD
    assert len(calls) == 1


def test_player_info_for_an_unknown_player_is_none_without_retrying() -> None:
    # Real example: player 79635 (Bobby Watson, 1954-55); the source sends an empty body.
    calls = []

    def fake(player_id, timeout):
        calls.append(player_id)
        return {}

    assert fetch_player_info(79635, request=fake, sleep=lambda s: None) is None
    assert calls == [79635]


def test_player_info_retries_real_failures() -> None:
    outcomes = [requests.Timeout(), GOOD]

    def fake(player_id, timeout):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    sleeps = []
    assert fetch_player_info(893, request=fake, sleep=sleeps.append) == GOOD
    assert sleeps == [5.0]
