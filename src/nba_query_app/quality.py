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

# NBA champions to spot-check the inferred playoff rounds against:
# (season, champion's team_id, name at the time).
KNOWN_CHAMPIONS = [
    ("1946-47", 1610612744, "Philadelphia Warriors"),
    ("1953-54", 1610612747, "Minneapolis Lakers"),  # after a round robin
    ("1961-62", 1610612738, "Boston Celtics"),
    ("1966-67", 1610612755, "Philadelphia 76ers"),
    ("1970-71", 1610612749, "Milwaukee Bucks"),
    ("1974-75", 1610612744, "Golden State Warriors"),
    ("1978-79", 1610612760, "Seattle SuperSonics"),
    ("1990-91", 1610612741, "Chicago Bulls"),
    ("1998-99", 1610612759, "San Antonio Spurs"),
    ("2015-16", 1610612739, "Cleveland Cavaliers"),
    ("2018-19", 1610612761, "Toronto Raptors"),
]

# From this season, every playoff game ID carries the round as its 8th digit
# (e.g. 0041500401: round 4); before, IDs are sequential or inconsistent.
STRUCTURED_PLAYOFF_IDS_FROM = "2001-02"


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


def _known_champions_query() -> str:
    """Report each KNOWN_CHAMPIONS entry whose Finals winner differs (skipped if not loaded)."""
    values = ", ".join(
        f"('{season}', {team_id}, '{name}')" for season, team_id, name in KNOWN_CHAMPIONS
    )
    return f"""
        SELECT k.season, 'expected the ' || k.name || ', data has '
                   || coalesce(s.winner_abbreviation, 'no Finals')
        FROM (VALUES {values}) AS k (season, team_id, name)
        LEFT JOIN nba.playoff_series s
            ON s.season = k.season AND s.playoff_round_name = 'Finals'
        WHERE EXISTS (
            SELECT FROM nba.games g WHERE g.season = k.season AND g.season_type = 'Playoffs'
        )
          AND s.winner_team_id IS DISTINCT FROM k.team_id
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
        "player_points_dont_match_team_score",
        "a team's player rows don't add up to its final score in the team log",
        """
        WITH players AS (
            SELECT game_id, team_id, sum(pts) AS pts
            FROM nba.player_game_stats GROUP BY game_id, team_id
        )
        SELECT t.game_id || ' ' || t.team_id,
               g.season || ' ' || t.team_abbreviation || ': players add up to '
                   || coalesce(p.pts::text, 'nothing') || ', team scored ' || t.pts
        FROM nba.team_game_stats t
        JOIN nba.games g USING (game_id)
        LEFT JOIN players p USING (game_id, team_id)
        WHERE p.pts IS DISTINCT FROM t.pts
        ORDER BY 1
        """,
    ),
    Check(
        "player_result_disagrees_with_team",
        "player rows whose W/L or home/away differs from their team's row in the team log",
        """
        SELECT s.game_id || ' ' || s.team_id,
               min(s.team_abbreviation) || ': ' || count(*) || ' player row(s) disagree'
        FROM nba.player_game_stats s
        JOIN nba.team_game_stats t USING (game_id, team_id)
        WHERE s.won <> t.won OR s.is_home IS DISTINCT FROM t.is_home
        GROUP BY s.game_id, s.team_id
        ORDER BY 1
        """,
    ),
    Check(
        "games_without_team_scores",
        "games without exactly two rows in the team log (team_game_stats)",
        """
        SELECT g.game_id, g.season || ' ' || g.season_type || ': '
                   || count(t.team_id) || ' team row(s)'
        FROM nba.games g LEFT JOIN nba.team_game_stats t USING (game_id)
        GROUP BY g.game_id
        HAVING count(t.team_id) <> 2
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
        "team_minutes_not_a_game_length",
        f"team-log minutes that aren't {REGULATION_TEAM_MINUTES} plus a whole number of "
        f"{OVERTIME_TEAM_MINUTES}-minute overtimes, or that differ between the two teams",
        f"""
        SELECT t.game_id || ' ' || t.team_id,
               g.season || ' ' || t.team_abbreviation || ': ' || t.minutes || ' minutes, '
                   || 'opponent ' || coalesce(o.minutes::text, 'not recorded')
        FROM nba.team_game_stats t
        JOIN nba.games g USING (game_id)
        LEFT JOIN nba.team_game_stats o
            ON o.game_id = t.game_id AND o.team_id = t.opponent_team_id
        WHERE t.minutes < {REGULATION_TEAM_MINUTES}
           OR (t.minutes - {REGULATION_TEAM_MINUTES}) %% {OVERTIME_TEAM_MINUTES} <> 0
           OR t.minutes <> o.minutes
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
        "playoff_games_without_round",
        "playoff games whose round couldn't be worked out (not a knockout bracket)",
        """
        SELECT game_id, season FROM nba.games
        WHERE season_type = 'Playoffs' AND playoff_round IS NULL
        ORDER BY 1
        """,
    ),
    Check(
        "playoff_round_differs_from_game_id",
        f"inferred playoff rounds that differ from the round in the game ID (from "
        f"{STRUCTURED_PLAYOFF_IDS_FROM}, when every playoff game ID carries it)",
        f"""
        SELECT game_id, season || ' ' || playoff_round_name || ': inferred round '
                   || playoff_round || ', game ID says ' || substr(game_id, 8, 1)
        FROM nba.games
        WHERE season_type = 'Playoffs' AND season >= '{STRUCTURED_PLAYOFF_IDS_FROM}'
          AND playoff_round IS DISTINCT FROM substr(game_id, 8, 1)::integer
        ORDER BY 1
        """,
    ),
    Check(
        "known_champions",
        "well-known NBA champions that the inferred Finals winner doesn't match",
        _known_champions_query(),
    ),
    Check(
        "players_without_details",
        "players with no row in player_details, or whose birth date was looked up but not found",
        """
        SELECT p.player_id::text,
               p.full_name || CASE WHEN d.player_id IS NULL THEN ': no details row'
                                   ELSE ': no birth date at NBA.com' END
        FROM nba.players p LEFT JOIN nba.player_details d USING (player_id)
        WHERE d.player_id IS NULL OR (d.birth_date_checked AND d.birth_date IS NULL)
        ORDER BY p.player_id
        """,
    ),
    Check(
        "implausible_age",
        "players younger than 18 or 46 and older in their first or last game (the "
        "oldest NBA player, Nat Hickey, was 45 in 1948)",
        """
        WITH span AS (
            SELECT s.player_id, min(g.game_date) AS first_game, max(g.game_date) AS last_game
            FROM nba.player_game_stats s JOIN nba.games g USING (game_id)
            GROUP BY s.player_id
        )
        SELECT p.player_id::text,
               p.full_name || ': born ' || d.birth_date || ', aged '
                   || date_part('year', age(sp.first_game, d.birth_date)) || ' to '
                   || date_part('year', age(sp.last_game, d.birth_date))
        FROM span sp
        JOIN nba.player_details d USING (player_id)
        JOIN nba.players p USING (player_id)
        WHERE age(sp.first_game, d.birth_date) < interval '18 years'
           OR age(sp.last_game, d.birth_date) >= interval '46 years'
        ORDER BY p.player_id
        """,
    ),
    Check(
        "drafted_after_first_game",
        "players drafted more than a year after their first season (the draft follows "
        "the season, and a few early players, and Spencer Haywood in 1970-71, played a "
        "season before being drafted)",
        """
        WITH debut AS (
            SELECT s.player_id, min(g.season) AS season
            FROM nba.player_game_stats s JOIN nba.games g USING (game_id)
            GROUP BY s.player_id
        )
        SELECT p.player_id::text,
               p.full_name || ': drafted ' || d.draft_year || ', first game in ' || db.season
        FROM debut db
        JOIN nba.player_details d USING (player_id)
        JOIN nba.players p USING (player_id)
        WHERE d.draft_year > left(db.season, 4)::integer + 1
        ORDER BY p.player_id
        """,
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
        "player_points_dont_match_team_score",
        game_ids_in("1946-47", "1995-96") + r" \d+",
        "old box scores whose player rows don't add up to the team's score (points off, or "
        "rows missing), and the players the source lost in 1990-96 (e.g. Loy Vaught)",
    ),
    KnownException(
        "player_points_dont_match_team_score",
        r"0029600070 \d+",
        "Sarunas Marciulionis (a Denver player that season) is listed under Cleveland; "
        "moving his 7 points and 29 minutes would fix both teams, but the source is kept",
    ),
    KnownException(
        "player_points_dont_match_team_score",
        r"0029800661 1610612751",
        "the Nets' player rows add up to 4 more than their score (97 vs 93); cause unknown",
    ),
    KnownException(
        "team_minutes_not_a_game_length",
        r"0026000250 \d+|0026100013 \d+|0026100029 \d+|0026100069 \d+|0022600231 \d+",
        "team-log minutes errors (239, 247, 249 and 239 in 1960-62; Denver 168 on "
        "2026-11-14, whose players add up to 242); the player rows are unaffected",
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
    KnownException(
        "players_without_details",
        r"79635|200603",
        "Bobby Watson (1952-53) isn't in NBA.com's player index at all, and Corey Williams "
        "(1992-94) has no birth date there",
    ),
    KnownException(
        "players_without_details",
        r"76548|76689|76769|76813|77234|77287|77499|77507|77557|77767|77807|77925|77979|78448",
        "14 players from 1946-47 to 1954-55 whose NBA.com birth date is a placeholder "
        "(early April 1905), stored as not recorded",
    ),
    KnownException(
        "drafted_after_first_game",
        r"78234",
        "Jim Springer (born 1926, played 1948-49) is listed as a 1956 draft pick, almost "
        "certainly someone else's pick; the source value is kept",
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
