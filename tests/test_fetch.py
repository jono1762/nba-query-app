"""Retry behaviour of the stats.nba.com downloader. No network: requests are faked."""

import pytest
import requests

from nba_query_app.ingest.fetch import FetchError, fetch_league_game_log

GOOD = {"resultSets": [{"headers": ["GAME_ID"], "rowSet": [["0022600001"]]}]}


class FakeRequests:
    """Returns (or raises) the given outcomes in order, recording each call."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, season, season_type, timeout):
        self.calls.append((season, season_type))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_returns_response_on_first_success() -> None:
    fake, sleeps = FakeRequests(GOOD), []
    assert fetch_league_game_log("2026-27", "Playoffs", request=fake, sleep=sleeps.append) == GOOD
    assert fake.calls == [("2026-27", "Playoffs")]
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

    with pytest.raises(FetchError, match="2026-27 Regular Season after 3 attempts"):
        fetch_league_game_log("2026-27", "Regular Season", request=fake, sleep=lambda s: None)
    assert len(fake.calls) == 3


def test_rejects_unknown_season_type() -> None:
    with pytest.raises(ValueError):
        fetch_league_game_log("2026-27", "Preseason", request=FakeRequests(), sleep=lambda s: None)
