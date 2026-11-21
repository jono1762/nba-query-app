"""Helpers for working out NBA seasons from dates."""

from datetime import date

# The regular season starts in October, so October-December belong to the
# season that starts that year, and January-September to the one before.
SEASON_START_MONTH = 10


def season_for_date(day: date) -> str:
    """Return the season label for a date, e.g. 2026-11-18 -> '2026-27'.

    The summer offseason (July-September) is counted as part of the season that
    just ended, which matches what "this season" means in a question asked then.
    """
    start_year = day.year if day.month >= SEASON_START_MONTH else day.year - 1
    end_year_short = str(start_year + 1)[-2:]
    return f"{start_year}-{end_year_short}"


def season_start_year(season: str) -> int:
    """'2026-27' -> 2026. Rejects anything that isn't a valid consecutive-year label."""
    parts = season.split("-")
    if len(parts) != 2 or not (parts[0].isdigit() and len(parts[0]) == 4):
        raise ValueError(f"Season must look like '2026-27', got {season!r}")
    start = int(parts[0])
    if parts[1] != str(start + 1)[-2:]:
        raise ValueError(f"Season {season!r} doesn't span consecutive years")
    return start


def seasons_between(first: str, last: str) -> list[str]:
    """Every season from first to last inclusive, e.g. ('2000-01', '2002-03') -> 3 seasons."""
    start, end = season_start_year(first), season_start_year(last)
    if start > end:
        raise ValueError(f"First season {first} is after last season {last}")
    return [f"{year}-{str(year + 1)[-2:]}" for year in range(start, end + 1)]
