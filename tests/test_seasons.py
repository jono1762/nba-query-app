from datetime import date

import pytest

from nba_query_app.seasons import season_for_date, seasons_between


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


def test_seasons_between() -> None:
    assert seasons_between("1999-00", "2001-02") == ["1999-00", "2000-01", "2001-02"]
    assert seasons_between("2026-27", "2026-27") == ["2026-27"]


@pytest.mark.parametrize(
    ("first", "last"),
    [
        ("2026-27", "2025-26"),  # backwards
        ("2026", "2026-27"),  # missing second half
        ("2026-28", "2026-27"),  # not consecutive years
        ("26-27", "2026-27"),  # two-digit start year
    ],
)
def test_seasons_between_rejects_bad_input(first: str, last: str) -> None:
    with pytest.raises(ValueError):
        seasons_between(first, last)
