from datetime import date

import pytest

from nba_query_app.seasons import season_for_date


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 11, 18), "2026-27"),  # early in the season
        (date(2027, 3, 1), "2026-27"),  # same season, after New Year
        (date(2026, 10, 1), "2026-27"),  # first day of the start month
        (date(2026, 9, 30), "2025-26"),  # offseason counts as the season just ended
        (date(1999, 12, 25), "1999-00"),  # century rollover
    ],
)
def test_season_for_date(day: date, expected: str) -> None:
    assert season_for_date(day) == expected
