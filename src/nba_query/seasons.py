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
