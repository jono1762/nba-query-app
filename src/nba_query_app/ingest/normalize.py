"""Turn a stats.nba.com LeagueGameLog response into rows for our tables.

The response has one row per player per game. From it we derive:
- teams and players (with the most recent name seen in this batch),
- games (date, season, home and away team),
- box score rows (with opponent, home/away and win/loss worked out).

Many stats weren't recorded in early seasons. Those arrive as null and stay None
("not recorded", which is different from 0). The one exception is minutes: early
seasons report 0 for every player, so a game where everyone has 0 minutes is
treated as "minutes not recorded". Likewise, an attempts figure lower than the
makes (impossible, but present in old box scores) is treated as not recorded,
a W/L missing from a single player row is filled in from the teammates, and a
player listed on both teams in one game is resolved to a single row.

This module does no network or database work, so it is easy to test.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

# SEASON_ID looks like '22026': the first digit is the season type, the rest the start year.
SEASON_TYPE_BY_PREFIX = {"2": "Regular Season", "4": "Playoffs", "5": "Play-In"}


class NormalizeError(ValueError):
    """The response doesn't look the way we expect; refuse rather than load bad data."""


@dataclass(frozen=True)
class Team:
    team_id: int
    abbreviation: str
    name: str
    name_as_of: date


@dataclass(frozen=True)
class Player:
    player_id: int
    full_name: str
    name_as_of: date


@dataclass(frozen=True)
class Game:
    game_id: str
    game_date: date
    season: str
    season_type: str
    home_team_id: int | None  # None at a neutral site
    away_team_id: int | None
    neutral_site: bool


@dataclass(frozen=True)
class PlayerGameStats:
    game_id: str
    player_id: int
    team_id: int
    team_abbreviation: str
    opponent_team_id: int
    is_home: bool | None  # None at a neutral site
    won: bool
    # Always recorded:
    pts: int
    fgm: int
    ftm: int
    # None means "not recorded" (common in early seasons):
    minutes: int | None
    fga: int | None
    fg3m: int | None
    fg3a: int | None
    fta: int | None
    oreb: int | None
    dreb: int | None
    reb: int | None
    ast: int | None
    stl: int | None
    blk: int | None
    tov: int | None
    pf: int | None
    plus_minus: int | None


@dataclass(frozen=True)
class Batch:
    teams: list[Team]
    players: list[Player]
    games: list[Game]
    stats: list[PlayerGameStats]


# Our column name -> stats.nba.com column name.
# Always present in the source; a missing value means the data is broken.
REQUIRED_STATS = {"pts": "PTS", "fgm": "FGM", "ftm": "FTM"}
# May be missing (not recorded), especially in early seasons.
OPTIONAL_STATS = {
    "minutes": "MIN",
    "fga": "FGA",
    "fg3m": "FG3M",
    "fg3a": "FG3A",
    "fta": "FTA",
    "oreb": "OREB",
    "dreb": "DREB",
    "reb": "REB",
    "ast": "AST",
    "stl": "STL",
    "blk": "BLK",
    "tov": "TOV",
    "pf": "PF",
    "plus_minus": "PLUS_MINUS",
}
PAIRS_MADE_ATTEMPTED = (("fgm", "fga"), ("fg3m", "fg3a"), ("ftm", "fta"))


def season_from_id(season_id: str) -> tuple[str, str]:
    """'22026' -> ('2026-27', 'Regular Season'); '42025' -> ('2025-26', 'Playoffs')."""
    prefix, year = season_id[:1], season_id[1:]
    if prefix not in SEASON_TYPE_BY_PREFIX or len(year) != 4 or not year.isdigit():
        raise NormalizeError(f"Unsupported SEASON_ID {season_id!r}")
    start = int(year)
    return f"{start}-{str(start + 1)[-2:]}", SEASON_TYPE_BY_PREFIX[prefix]


def parse_is_home(matchup: str) -> bool:
    """'OKC vs. SAS' means OKC is at home; 'OKC @ SAS' means OKC is away."""
    if " vs. " in matchup:
        return True
    if " @ " in matchup:
        return False
    raise NormalizeError(f"Can't tell home/away from MATCHUP {matchup!r}")


def parse_won(wl: str | None) -> bool | None:
    """'W' -> True, 'L' -> False, missing -> None (filled in from teammates later)."""
    if wl is None:
        return None
    if wl not in ("W", "L"):
        raise NormalizeError(f"Unexpected WL value {wl!r}")
    return wl == "W"


def normalize_league_game_log(response: dict[str, Any]) -> Batch:
    """Convert a LeagueGameLog (player mode) response into table rows."""
    result = response["resultSets"][0]
    headers: list[str] = result["headers"]
    raw_rows = [dict(zip(headers, values, strict=True)) for values in result["rowSet"]]
    raw_rows = _resolve_duplicate_player_rows(raw_rows)

    home_away = _home_and_away_by_game(raw_rows)
    minutes_missing = _games_without_minutes(raw_rows)
    team_won = _results_by_game_and_team(raw_rows, home_away)

    teams: dict[int, Team] = {}
    players: dict[int, Player] = {}
    games: dict[str, Game] = {}
    stats: list[PlayerGameStats] = []

    for row in raw_rows:
        game_id = row["GAME_ID"]
        game_date = date.fromisoformat(row["GAME_DATE"])
        season, season_type = season_from_id(row["SEASON_ID"])
        team_id = row["TEAM_ID"]
        home_id, away_id, opponent_of = home_away[game_id]
        opponent_id = opponent_of[team_id]
        is_home = None if home_id is None else team_id == home_id

        _keep_latest(
            teams, team_id, Team(team_id, row["TEAM_ABBREVIATION"], row["TEAM_NAME"], game_date)
        )
        _keep_latest(
            players, row["PLAYER_ID"], Player(row["PLAYER_ID"], row["PLAYER_NAME"], game_date)
        )

        game = Game(
            game_id=game_id,
            game_date=game_date,
            season=season,
            season_type=season_type,
            home_team_id=home_id,
            away_team_id=away_id,
            neutral_site=home_id is None,
        )
        if games.setdefault(game_id, game) != game:
            raise NormalizeError(f"Rows for game {game_id} disagree about date or season")

        counting = {column: row[source] for column, source in OPTIONAL_STATS.items()}
        if game_id in minutes_missing:
            counting["minutes"] = None
        for column, source in REQUIRED_STATS.items():
            if row[source] is None:
                raise NormalizeError(f"Missing {source} for player {row['PLAYER_ID']} in {game_id}")
            counting[column] = row[source]
        _drop_impossible_attempts(counting)

        stats.append(
            PlayerGameStats(
                game_id=game_id,
                player_id=row["PLAYER_ID"],
                team_id=team_id,
                team_abbreviation=row["TEAM_ABBREVIATION"],
                opponent_team_id=opponent_id,
                is_home=is_home,
                won=team_won[(game_id, team_id)],
                **counting,
            )
        )

    keys = [(s.game_id, s.player_id) for s in stats]
    if len(keys) != len(set(keys)):  # would silently overwrite each other in the database
        raise NormalizeError("A player appears more than once in the same game")

    return Batch(
        teams=list(teams.values()),
        players=list(players.values()),
        games=list(games.values()),
        stats=stats,
    )


def _home_and_away_by_game(
    raw_rows: list[dict[str, Any]],
) -> dict[str, tuple[int | None, int | None, dict[int, int]]]:
    """For each game: (home team, away team, {team: its opponent}).

    Home and away are None for neutral-site games, where both teams are listed as away.
    Each team's home/away is the majority of its players' rows: old box scores
    occasionally mislabel a couple of rows (e.g. 10 say '@', 2 say 'vs.').
    """
    votes: dict[str, dict[int, list[int]]] = {}  # game -> team -> [home votes, away votes]
    for row in raw_rows:
        tally = votes.setdefault(row["GAME_ID"], {}).setdefault(row["TEAM_ID"], [0, 0])
        tally[0 if parse_is_home(row["MATCHUP"]) else 1] += 1

    is_home_by_game: dict[str, dict[int, bool]] = {}
    for game_id, teams in votes.items():
        for team_id, (home_votes, away_votes) in teams.items():
            if home_votes == away_votes:
                raise NormalizeError(f"Rows for game {game_id} disagree about home/away")
            is_home_by_game.setdefault(game_id, {})[team_id] = home_votes > away_votes

    result = {}
    for game_id, flags in is_home_by_game.items():
        if len(flags) != 2:
            raise NormalizeError(f"Game {game_id} has {len(flags)} teams, expected 2")
        (team_a, a_home), (team_b, b_home) = flags.items()
        opponent_of = {team_a: team_b, team_b: team_a}
        if a_home != b_home:
            home, away = (team_a, team_b) if a_home else (team_b, team_a)
            result[game_id] = (home, away, opponent_of)
        elif not a_home:
            result[game_id] = (None, None, opponent_of)  # neutral site
        else:
            raise NormalizeError(f"Both teams are listed as home in game {game_id}")
    return result


def _real_stats(row: dict[str, Any]) -> tuple:
    """The row's stat values with "not recorded" and 0 treated alike, for comparison."""
    return tuple(
        row[source] or 0 for source in [*REQUIRED_STATS.values(), *OPTIONAL_STATS.values()]
    )


def _resolve_duplicate_player_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Old box scores occasionally list one player on both teams in the same game.

    Keep the copy with real stats (e.g. Bob Cousy 1951-52: 13 points for BOS, all
    zeros for BAL). If the copies are identical, keep the team the player was on in
    his nearest other game that season. If that can't decide it, drop the copies
    when they are all zeros (nothing lost); otherwise refuse.
    """
    by_key: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in raw_rows:
        by_key.setdefault((row["GAME_ID"], row["PLAYER_ID"]), []).append(row)
    if all(len(copies) == 1 for copies in by_key.values()):
        return raw_rows

    drop: set[int] = set()  # id() of rows to discard
    for (game_id, player_id), copies in by_key.items():
        if len(copies) == 1:
            continue
        with_stats = [r for r in copies if any(_real_stats(r))]
        if len(with_stats) == 1:
            keep = with_stats[0]
        elif len({_real_stats(r) for r in copies}) == 1:
            keep = _copy_matching_nearest_game(copies, raw_rows)
            if keep is None and not with_stats:
                drop.update(id(r) for r in copies)
                continue
            if keep is None:
                raise NormalizeError(
                    f"Can't tell which team player {player_id} was on in {game_id}"
                )
        else:
            raise NormalizeError(f"Player {player_id} has conflicting rows in game {game_id}")
        drop.update(id(r) for r in copies if r is not keep)
    return [row for row in raw_rows if id(row) not in drop]


def _copy_matching_nearest_game(
    copies: list[dict[str, Any]], raw_rows: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The copy whose team matches the player's team in his closest other game by date."""
    game_id, player_id = copies[0]["GAME_ID"], copies[0]["PLAYER_ID"]
    day = date.fromisoformat(copies[0]["GAME_DATE"])
    others = [r for r in raw_rows if r["PLAYER_ID"] == player_id and r["GAME_ID"] != game_id]
    if not others:
        return None
    nearest = min(others, key=lambda r: abs((date.fromisoformat(r["GAME_DATE"]) - day).days))
    matches = [r for r in copies if r["TEAM_ID"] == nearest["TEAM_ID"]]
    return matches[0] if len(matches) == 1 else None


def _results_by_game_and_team(
    raw_rows: list[dict[str, Any]],
    home_away: dict[str, tuple[int | None, int | None, dict[int, int]]],
) -> dict[tuple[str, int], bool]:
    """Whether each team won each game, taken from its players' W/L values.

    Old box scores occasionally miss W/L on a single player row, so a team's result
    comes from any teammate, or failing that, the opposite of the opponent's result.
    """
    seen: dict[tuple[str, int], set[bool]] = {}
    for row in raw_rows:
        won = parse_won(row["WL"])
        results = seen.setdefault((row["GAME_ID"], row["TEAM_ID"]), set())
        if won is not None:
            results.add(won)

    team_won = {}
    for (game_id, team_id), results in seen.items():
        opponent_results = seen.get((game_id, home_away[game_id][2][team_id]), set())
        if len(results) > 1 or len(opponent_results) > 1:
            raise NormalizeError(f"Players disagree about who won game {game_id}")
        if results:
            team_won[(game_id, team_id)] = next(iter(results))
        elif opponent_results:
            team_won[(game_id, team_id)] = not next(iter(opponent_results))
        else:
            raise NormalizeError(f"No W/L result for either team in game {game_id}")
    return team_won


def _drop_impossible_attempts(counting: dict[str, int | None]) -> None:
    """Old box scores sometimes show fewer attempts than makes (e.g. 1 FT made, 0 attempted).

    The attempts figure is the unreliable one (often a 0 meaning "not recorded"),
    so it becomes None. The makes are kept because points depend on them.
    """
    for made, attempted in PAIRS_MADE_ATTEMPTED:
        if (
            counting[made] is not None
            and counting[attempted] is not None
            and counting[attempted] < counting[made]
        ):
            counting[attempted] = None


def _games_without_minutes(raw_rows: list[dict[str, Any]]) -> set[str]:
    """Games where every player shows 0 minutes: minutes weren't recorded, not truly 0."""
    has_minutes: dict[str, bool] = {}
    for row in raw_rows:
        played = bool(row["MIN"])  # None or 0 -> False
        has_minutes[row["GAME_ID"]] = has_minutes.get(row["GAME_ID"], False) or played
    return {game_id for game_id, any_minutes in has_minutes.items() if not any_minutes}


def _keep_latest(found: dict, key: int, candidate: Team | Player) -> None:
    """Store the candidate unless we already hold a version seen on a later date."""
    current = found.get(key)
    if current is None or candidate.name_as_of > current.name_as_of:
        found[key] = candidate
