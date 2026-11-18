"""Turn a stats.nba.com LeagueGameLog response into rows for our tables.

The response has one row per player per game. From it we derive:
- teams and players (with the most recent name seen in this batch),
- games (date, season, home and away team),
- box score rows (with opponent, home/away and win/loss worked out).

This module does no network or database work, so it is easy to test.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

# SEASON_ID looks like '22026': the first digit is the season type, the rest the start year.
SEASON_TYPE_BY_PREFIX = {"2": "Regular Season", "4": "Playoffs"}


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
    home_team_id: int
    away_team_id: int


@dataclass(frozen=True)
class PlayerGameStats:
    game_id: str
    player_id: int
    team_id: int
    team_abbreviation: str
    opponent_team_id: int
    is_home: bool
    won: bool
    minutes: int
    pts: int
    fgm: int
    fga: int
    fg3m: int
    fg3a: int
    ftm: int
    fta: int
    oreb: int
    dreb: int
    reb: int
    ast: int
    stl: int
    blk: int
    tov: int
    pf: int
    plus_minus: int | None


@dataclass(frozen=True)
class Batch:
    teams: list[Team]
    players: list[Player]
    games: list[Game]
    stats: list[PlayerGameStats]


# Our column name -> stats.nba.com column name, for the plain counting stats.
COUNTING_STATS = {
    "minutes": "MIN",
    "pts": "PTS",
    "fgm": "FGM",
    "fga": "FGA",
    "fg3m": "FG3M",
    "fg3a": "FG3A",
    "ftm": "FTM",
    "fta": "FTA",
    "oreb": "OREB",
    "dreb": "DREB",
    "reb": "REB",
    "ast": "AST",
    "stl": "STL",
    "blk": "BLK",
    "tov": "TOV",
    "pf": "PF",
}


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


def parse_won(wl: str) -> bool:
    if wl not in ("W", "L"):
        raise NormalizeError(f"Unexpected WL value {wl!r}")
    return wl == "W"


def normalize_league_game_log(response: dict[str, Any]) -> Batch:
    """Convert a LeagueGameLog (player mode) response into table rows."""
    result = response["resultSets"][0]
    headers: list[str] = result["headers"]
    raw_rows = [dict(zip(headers, values, strict=True)) for values in result["rowSet"]]

    teams_by_game: dict[str, set[int]] = {}
    for row in raw_rows:
        teams_by_game.setdefault(row["GAME_ID"], set()).add(row["TEAM_ID"])
    for game_id, team_ids in teams_by_game.items():
        if len(team_ids) != 2:
            raise NormalizeError(f"Game {game_id} has {len(team_ids)} teams, expected 2")

    teams: dict[int, Team] = {}
    players: dict[int, Player] = {}
    games: dict[str, Game] = {}
    stats: list[PlayerGameStats] = []

    for row in raw_rows:
        game_id = row["GAME_ID"]
        game_date = date.fromisoformat(row["GAME_DATE"])
        season, season_type = season_from_id(row["SEASON_ID"])
        team_id = row["TEAM_ID"]
        (opponent_id,) = teams_by_game[game_id] - {team_id}
        is_home = parse_is_home(row["MATCHUP"])

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
            home_team_id=team_id if is_home else opponent_id,
            away_team_id=opponent_id if is_home else team_id,
        )
        if games.setdefault(game_id, game) != game:
            raise NormalizeError(f"Rows for game {game_id} disagree about date/season/home team")

        counting = {}
        for column, source in COUNTING_STATS.items():
            value = row[source]
            if value is None:
                raise NormalizeError(f"Missing {source} for player {row['PLAYER_ID']} in {game_id}")
            counting[column] = value

        stats.append(
            PlayerGameStats(
                game_id=game_id,
                player_id=row["PLAYER_ID"],
                team_id=team_id,
                team_abbreviation=row["TEAM_ABBREVIATION"],
                opponent_team_id=opponent_id,
                is_home=is_home,
                won=parse_won(row["WL"]),
                plus_minus=row["PLUS_MINUS"],
                **counting,
            )
        )

    return Batch(
        teams=list(teams.values()),
        players=list(players.values()),
        games=list(games.values()),
        stats=stats,
    )


def _keep_latest(found: dict, key: int, candidate: Team | Player) -> None:
    """Store the candidate unless we already hold a version seen on a later date."""
    current = found.get(key)
    if current is None or candidate.name_as_of > current.name_as_of:
        found[key] = candidate
