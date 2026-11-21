"""Data-quality checks: look for loaded data that is impossible or suspicious.

Each check is a SQL query that returns one row per violation: a short key (e.g.
a game ID, or a season) and a human-readable detail. The checks report; they
never block a load or change data. When something is reported, look at the
actual rows and decide: fix the cleanup rules in ingest/normalize.py, or, if
the data is genuinely right (or a known, unfixable source error), add it to
KNOWN_EXCEPTIONS with the reason, so the report stays quiet and anything new
stands out.

Usage:
    uv run --env-file .env python -m nba_query_app.quality

The backfill runs these checks automatically at the end. The exit code is 1 if
anything not in KNOWN_EXCEPTIONS was found, so a scheduled job can notice.
"""

import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import psycopg

from nba_query_app.ingest.normalize import (
    OVERTIME_TEAM_MINUTES,
    REGULATION_TEAM_MINUTES,
    TEAM_MINUTES_SHORTFALL,
)
from nba_query_app.seasons import season_for_date, season_start_year

# Below this share of player rows, a stat counts as not fully recorded that season.
# Must match the threshold in the nba.stat_tracked_since view (migration 010).
FULLY_RECORDED_PCT = 98

# The highest single-game values in NBA history (regular season and playoffs).
# Anything above one of these is either a new record or a data error.
SINGLE_GAME_RECORDS = {
    "pts": 100,  # Wilt Chamberlain, 1962-03-02
    "reb": 55,  # Wilt Chamberlain, 1960-11-24
    "ast": 30,  # Scott Skiles, 1990-12-30
    "stl": 11,  # Larry Kenon 1976, Kendall Gill 1999
    "blk": 17,  # Elmore Smith, 1973-10-28
    "fg3m": 14,  # Klay Thompson, 2018-10-29
    "tov": 14,  # John Drew 1978, Jason Kidd 2000
    "minutes": 69,  # Dale Ellis, 1989-11-09 (five overtimes)
}

# Well-known official regular-season figures, to spot-check the data against:
# (player_id, name, season or None for the whole career, stat, official value).
KNOWN_TOTALS = [
    (76003, "Kareem Abdul-Jabbar", None, "pts", 38387),
    (893, "Michael Jordan", None, "pts", 32292),
    (76375, "Wilt Chamberlain", None, "pts", 31419),
    (304, "John Stockton", None, "ast", 15806),
    (304, "John Stockton", None, "stl", 3265),
    (165, "Hakeem Olajuwon", None, "blk", 3830),
    (76375, "Wilt Chamberlain", "1961-62", "pts", 4029),
    (76375, "Wilt Chamberlain", "1960-61", "reb", 2149),
    (977, "Kobe Bryant", "2005-06", "pts", 2832),
    (201939, "Stephen Curry", "2015-16", "fg3m", 402),
    (76631, "Mark Eaton", "1984-85", "blk", 456),
]


@dataclass(frozen=True)
class Check:
    name: str
    description: str
    # Returns (key, detail) rows. May use the %(current_season)s parameter, so a
    # literal percent sign must be written %%.
    query: str


@dataclass(frozen=True)
class KnownException:
    """A reported violation that has been looked at and accepted."""

    check: str
    key_pattern: str  # regular expression that must match the whole key
    reason: str

    def matches(self, check: str, key: str) -> bool:
        return check == self.check and re.fullmatch(self.key_pattern, key) is not None


@dataclass(frozen=True)
class CheckResult:
    check: Check
    violations: list[tuple[str, str]]  # (key, detail), not explained by a known exception
    excused: list[tuple[str, str, str]]  # (key, detail, reason)


def _extreme_value_query() -> str:
    parts = [
        f"SELECT game_id, player_id, '{stat}' AS stat, {stat} AS value, {record} AS record "
        f"FROM nba.player_game_stats WHERE {stat} > {record}"
        for stat, record in SINGLE_GAME_RECORDS.items()
    ]
    return f"""
        SELECT x.game_id || ' ' || x.player_id || ' ' || x.stat,
               p.full_name || ': ' || x.value || ' ' || x.stat || ' (record ' || x.record || ')'
        FROM ({" UNION ALL ".join(parts)}) x
        JOIN nba.players p USING (player_id)
        ORDER BY 1
    """


def _known_totals_query() -> str:
    """Add up the game rows for each KNOWN_TOTALS entry and report the ones that differ.

    Entries with no rows at all (player or season not loaded) are skipped.
    """
    parts = [
        f"SELECT '{name}' || ' ' || {f"'{season}'" if season else "'career'"} || ' ' || '{stat}'"
        f" AS key, sum(s.{stat}) AS actual, {expected} AS expected "
        f"FROM nba.player_game_stats s JOIN nba.games g USING (game_id) "
        f"WHERE s.player_id = {player_id} AND g.season_type = 'Regular Season'"
        + (f" AND g.season = '{season}'" if season else "")
        for player_id, name, season, stat, expected in KNOWN_TOTALS
    ]
    return f"""
        SELECT key, 'data has ' || actual || ', official ' || expected
        FROM ({" UNION ALL ".join(parts)}) x
        WHERE actual <> expected
        ORDER BY 1
    """


CHECKS = [
    Check(
        "three_pointers_before_the_line",
        "3-pointers recorded before 1979-80, when the NBA introduced the 3-point line",
        """
        SELECT s.game_id || ' ' || s.player_id,
               g.season || ': ' || coalesce(s.fg3m, 0) || ' of ' || coalesce(s.fg3a, 0) || ' 3PT'
        FROM nba.player_game_stats s JOIN nba.games g USING (game_id)
        WHERE g.season < '1979-80' AND (s.fg3m > 0 OR s.fg3a > 0)
        ORDER BY 1
        """,
    ),
    Check(
        "game_type_before_it_existed",
        "play-in games before 2019-20, or NBA Cup games before 2023-24",
        """
        SELECT game_id, season || ' ' || season_type || coalesce(' ' || nba_cup_round, '')
        FROM nba.games
        WHERE (season_type = 'Play-In' AND season < '2019-20')
           OR ((season_type = 'NBA Cup Final' OR nba_cup_round IS NOT NULL)
               AND season < '2023-24')
        ORDER BY 1
        """,
    ),
    Check(
        "stat_disappears",
        f"a stat drops below {FULLY_RECORDED_PCT}% of rows after first reaching it",
        f"""
        WITH first_full AS (
            SELECT stat, min(season) AS season
            FROM nba.stat_coverage WHERE pct_recorded >= {FULLY_RECORDED_PCT}
            GROUP BY stat
        )
        SELECT c.stat || ' ' || c.season,
               c.pct_recorded || '%% recorded (first reached {FULLY_RECORDED_PCT}%% in '
                   || f.season || ')'
        FROM nba.stat_coverage c JOIN first_full f USING (stat)
        WHERE c.season > f.season AND c.recorded_rows < {FULLY_RECORDED_PCT / 100} * c.player_rows
        ORDER BY 1
        """,
    ),
    Check(
        "points_dont_add_up",
        "points differ from 2 x 2-pointers + 3 x 3-pointers + free throws made",
        # FGM includes 3-pointers, so 2-pointers are FGM - FG3M. Before the 3-point
        # line (1979-80) there were no 3-pointers, so a missing FG3M counts as 0.
        """
        SELECT s.game_id || ' ' || s.player_id,
               p.full_name || ': ' || s.pts || ' pts but ' || s.fgm || ' FGM (' ||
                   coalesce(s.fg3m, 0) || ' 3PM), ' || s.ftm || ' FTM'
        FROM nba.player_game_stats s
        JOIN nba.games g USING (game_id)
        JOIN nba.players p USING (player_id)
        WHERE (s.fg3m IS NOT NULL OR g.season < '1979-80')
          AND s.pts <> 2 * (s.fgm - coalesce(s.fg3m, 0)) + 3 * coalesce(s.fg3m, 0) + s.ftm
        ORDER BY 1
        """,
    ),
    Check(
        "parts_exceed_whole",
        "rebounds differ from offensive + defensive, or more 3-pointers than field goals",
        """
        SELECT s.game_id || ' ' || s.player_id,
               p.full_name || ': ' || concat_ws(', ',
                   CASE WHEN s.reb <> s.oreb + s.dreb THEN
                       s.reb || ' reb but ' || s.oreb || ' off + ' || s.dreb || ' def' END,
                   CASE WHEN s.fg3m > s.fgm THEN s.fg3m || ' 3PM > ' || s.fgm || ' FGM' END,
                   CASE WHEN s.fg3a > s.fga THEN s.fg3a || ' 3PA > ' || s.fga || ' FGA' END)
        FROM nba.player_game_stats s JOIN nba.players p USING (player_id)
        WHERE s.reb <> s.oreb + s.dreb OR s.fg3m > s.fgm OR s.fg3a > s.fga
        ORDER BY 1
        """,
    ),
    Check(
        "above_single_game_record",
        "a single-game value above the all-time NBA record",
        _extreme_value_query(),
    ),
    Check(
        "result_doesnt_match_points",
        "a game whose two teams don't have one winner and one loser, "
        "or whose winner scored fewer points (adding up player rows)",
        """
        WITH teams AS (
            SELECT game_id, team_id, bool_and(won) AS won, bool_or(won) AS any_won,
                   sum(pts) AS pts
            FROM nba.player_game_stats GROUP BY game_id, team_id
        )
        SELECT game_id,
               string_agg(t.abbreviation || ' ' || teams.pts ||
                   CASE WHEN teams.won THEN ' (W)' WHEN teams.any_won THEN ' (W/L mixed)'
                        ELSE ' (L)' END, ', ' ORDER BY teams.team_id)
        FROM teams JOIN nba.teams t USING (team_id)
        GROUP BY game_id
        HAVING count(*) <> 2
            OR count(*) FILTER (WHERE teams.won) <> 1
            OR count(*) FILTER (WHERE teams.any_won AND NOT teams.won) > 0
            OR max(teams.pts) FILTER (WHERE teams.won)
               <= max(teams.pts) FILTER (WHERE NOT teams.any_won)
        ORDER BY 1
        """,
    ),
    Check(
        "team_minutes_short",
        f"a team's player minutes in a game fall {TEAM_MINUTES_SHORTFALL}+ short of the game "
        f"length ({REGULATION_TEAM_MINUTES}, plus {OVERTIME_TEAM_MINUTES} per overtime), in "
        "seasons where minutes are fully recorded: a player row missing from the source, "
        "or wrong minutes",
        # Game length comes from the team with more minutes (as in normalize.py).
        # Minutes are rounded to whole numbers per player, so a few minutes' shortfall
        # is just rounding. Teams with no minutes recorded at all are skipped; a
        # player without recorded minutes counts as 0 and is noted in the detail.
        f"""
        WITH teams AS (
            SELECT s.game_id, s.team_id, sum(s.minutes) AS minutes,
                   count(*) - count(s.minutes) AS unrecorded
            FROM nba.player_game_stats s
            JOIN nba.games g USING (game_id)
            WHERE g.season >= (
                SELECT tracked_since FROM nba.stat_tracked_since WHERE stat = 'minutes'
            )
            GROUP BY s.game_id, s.team_id
            HAVING count(s.minutes) > 0
        ),
        games AS (
            SELECT game_id,
                   {REGULATION_TEAM_MINUTES} + {OVERTIME_TEAM_MINUTES} * greatest(0, round(
                       (max(minutes) - {REGULATION_TEAM_MINUTES}) / {OVERTIME_TEAM_MINUTES}.0
                   )) AS length
            FROM teams GROUP BY game_id
        )
        SELECT t.game_id || ' ' || t.team_id,
               g.season || ' ' || tm.abbreviation || ': ' || t.minutes || ' of '
                   || gl.length || ' minutes'
                   || CASE WHEN t.unrecorded > 0
                           THEN ' (' || t.unrecorded || ' player(s) without minutes)'
                           ELSE '' END
        FROM teams t
        JOIN games gl USING (game_id)
        JOIN nba.games g USING (game_id)
        JOIN nba.teams tm USING (team_id)
        WHERE t.minutes <= gl.length - {TEAM_MINUTES_SHORTFALL}
        ORDER BY 1
        """,
    ),
    Check(
        "uneven_games_per_team",
        "teams with a different number of regular-season games from most teams that "
        "season (the current season is skipped, since it's still being played)",
        """
        WITH counts AS (
            SELECT g.season, s.team_id, max(s.team_abbreviation) AS team,
                   count(DISTINCT g.game_id) AS games
            FROM nba.player_game_stats s JOIN nba.games g USING (game_id)
            WHERE g.season_type = 'Regular Season' AND g.season <> %(current_season)s
            GROUP BY g.season, s.team_id
        ),
        usual AS (
            SELECT season, mode() WITHIN GROUP (ORDER BY games) AS games
            FROM counts GROUP BY season
        )
        SELECT c.season,
               string_agg(c.team || ' ' || c.games, ', ' ORDER BY c.team)
                   || ' (most teams ' || u.games || ')'
        FROM counts c JOIN usual u USING (season)
        WHERE c.games <> u.games
        GROUP BY c.season, u.games
        ORDER BY 1
        """,
    ),
    Check(
        "short_schedule",
        "seasons since 1967-68 (when the schedule reached 82 games) where most teams "
        "didn't play 82 regular-season games (the current season is skipped)",
        """
        WITH counts AS (
            SELECT g.season, s.team_id, count(DISTINCT g.game_id) AS games
            FROM nba.player_game_stats s JOIN nba.games g USING (game_id)
            WHERE g.season_type = 'Regular Season' AND g.season >= '1967-68'
              AND g.season <> %(current_season)s
            GROUP BY g.season, s.team_id
        )
        SELECT season, 'most teams played ' || mode() WITHIN GROUP (ORDER BY games)
        FROM counts GROUP BY season
        HAVING mode() WITHIN GROUP (ORDER BY games) <> 82
        ORDER BY 1
        """,
    ),
    Check(
        "known_totals",
        "well-known official regular-season totals (e.g. Kareem's 38,387 career points) "
        "that the game rows don't add up to",
        _known_totals_query(),
    ),
    Check(
        "cup_final_teams",
        "an NBA Cup final team that didn't win an NBA Cup semifinal that season",
        """
        WITH finalists AS (
            SELECT DISTINCT g.game_id, g.season, s.team_id, s.team_abbreviation
            FROM nba.games g JOIN nba.player_game_stats s USING (game_id)
            WHERE g.season_type = 'NBA Cup Final'
        )
        SELECT f.game_id, f.season || ' ' || f.team_abbreviation
        FROM finalists f
        WHERE NOT EXISTS (
            SELECT FROM nba.games g JOIN nba.player_game_stats s USING (game_id)
            WHERE g.season = f.season AND g.nba_cup_round = 'Semifinal'
              AND s.team_id = f.team_id AND s.won
        )
        ORDER BY 1
        """,
    ),
]


def game_ids_in(first_season: str, last_season: str) -> str:
    """A regex matching game IDs from these seasons: the ID's 4th-5th digits are the
    season's start year, e.g. 0024600028 is from 1946-47."""
    years = range(season_start_year(first_season), season_start_year(last_season) + 1)
    return r"00\d(?:" + "|".join(f"{year % 100:02d}" for year in years) + r")\d{5}"


LAC, BKN = 1610612746, 1610612751

# Violations that were looked at and are genuinely in the source data (or genuinely
# happened). Each needs a reason. See spikes/FINDINGS.md for the investigation.
KNOWN_EXCEPTIONS = [
    KnownException(
        "stat_disappears",
        r"pf (?:195[2-9]|19[67]\d)-\d\d",
        "personal fouls were only partly recorded until 1980-81 (79% to 99% per season)",
    ),
    KnownException(
        "points_dont_add_up",
        game_ids_in("1946-47", "1963-64") + r" \d+",
        "errors in old box scores (71 rows, none after 1963-64); nothing shows whether "
        "the points or the makes are wrong, so the source values are kept",
    ),
    KnownException(
        "parts_exceed_whole",
        r"0028500003 76713|0028500175 892|0028700330 149|0028700330 76686",
        "four 1985-88 box scores with more 3-pointers than field goals in the source",
    ),
    KnownException(
        "result_doesnt_match_points",
        game_ids_in("1946-47", "1989-90"),
        "old box scores whose player rows don't add up to the team score: player points "
        "are off (the source's team log confirms the W/L) or a player row is missing",
    ),
    KnownException(
        "result_doesnt_match_points",
        game_ids_in("1990-91", "1995-96"),
        "games missing a player the source lost (no player ID in the box score), e.g. "
        "Loy Vaught for the Clippers 1990-91 to 1995-96",
    ),
    KnownException(
        "team_minutes_short",
        game_ids_in("1976-77", "1985-86") + r" \d+",
        "players without recorded minutes (e.g. Eddie Johnson, Kansas City 1984-85: points "
        "but no minutes in 73 games) or rows missing, in box scores before 1986-87",
    ),
    KnownException(
        "team_minutes_short",
        game_ids_in("1986-87", "1996-97") + r" \d+",
        "players the source lost (their box-score line has no player ID): Loy Vaught for "
        "the Clippers 1990-91 to 1995-96, one Nets player in 1995-96, and probably the "
        "same cause in the few other games (not checked one by one)",
    ),
    KnownException(
        "team_minutes_short",
        game_ids_in("2005-06", "2006-07") + r" \d+",
        "wrong minutes in the source's season log (the game's box score has the right "
        "ones, e.g. Michael Redd 20 instead of 40:00 on 0020600111)",
    ),
    KnownException(
        "known_totals",
        r"Wilt Chamberlain career pts|Hakeem Olajuwon career blk|Wilt Chamberlain 1960-61 reb",
        "the source's game logs fall slightly short of the official totals (2 points, 5 "
        "blocks, 76 rebounds); not investigated game by game, kept as they are",
    ),
    KnownException(
        "uneven_games_per_team",
        r"1946-47|1956-57",
        "tiebreaker games played after the schedule ended counted as regular season "
        "(CHS-BOM 1947-03-31; STL-FTW and STL-MNL in March 1957)",
    ),
    KnownException(
        "uneven_games_per_team",
        r"1949-50|1950-51|1952-53|1953-54",
        "uneven schedules in the early league: the 17-team 1949-50 after the NBL merger, "
        "the Washington Capitols folding after 35 games in 1950-51, and 69 to 72 games "
        "per team in 1952-54",
    ),
    KnownException(
        "uneven_games_per_team",
        r"2012-13",
        "BOS-IND was cancelled after the Boston Marathon bombing and never made up",
    ),
    KnownException(
        "uneven_games_per_team",
        r"2019-20",
        "COVID-19: the season was suspended and only 22 teams resumed play",
    ),
    KnownException(
        "short_schedule",
        r"1998-99|2011-12",
        "lockout-shortened seasons (50 and 66 games)",
    ),
    KnownException(
        "short_schedule",
        r"2019-20|2020-21",
        "COVID-19-shortened seasons (2020-21 was scheduled at 72 games)",
    ),
]


def run_checks(
    conn: psycopg.Connection,
    current_season: str,
    checks: list[Check] = CHECKS,
    exceptions: list[KnownException] = KNOWN_EXCEPTIONS,
) -> list[CheckResult]:
    """Run every check. Read-only; nothing in the database changes."""
    results = []
    for check in checks:
        rows = conn.execute(check.query, {"current_season": current_season}).fetchall()
        violations, excused = [], []
        for key, detail in rows:
            reason = next((e.reason for e in exceptions if e.matches(check.name, key)), None)
            if reason is None:
                violations.append((key, detail))
            else:
                excused.append((key, detail, reason))
        results.append(CheckResult(check, violations, excused))
    return results


def report(results: list[CheckResult], log: Callable[[str], None] = print, limit: int = 20) -> int:
    """Print the results. Returns the number of unexplained violations."""
    total = 0
    for result in results:
        found = len(result.violations)
        total += found
        status = "ok" if not found else f"{found} found"
        excused = f" ({len(result.excused)} known)" if result.excused else ""
        log(f"[{status}{excused}] {result.check.name}: {result.check.description}")
        for key, detail in result.violations[:limit]:
            log(f"    {key}: {detail}")
        if found > limit:
            log(f"    ... and {found - limit} more")
    log(f"Data-quality checks: {total} unexplained violation(s).")
    return total


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        found = report(run_checks(conn, season_for_date(date.today())))
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
