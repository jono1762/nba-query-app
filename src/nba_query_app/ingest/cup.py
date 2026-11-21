"""NBA Cup (in-season tournament) games: which round each one was.

stats.nba.com's "IST" season type returns every Cup game of a season, one row per
player per game. Only the final is its own season type (SEASON_ID prefix 6); the
group, quarterfinal and semifinal games are regular-season games that the regular
season download already stores. So from the IST response we:
- keep only the final's rows to load as box scores (`final_only`), and
- work out every game's round, to tag the stored games (`cup_rounds`).

How rounds are worked out: every team plays exactly 4 group games, so a team's
5th Cup game is its quarterfinal, its 6th the semifinal and its 7th the final.
Both teams in a knockout game must agree on the round. This also works while a
tournament is still in progress. No network or database work happens here.
"""

from typing import Any

FINAL_SEASON_ID_PREFIX = "6"
GROUP_GAMES_PER_TEAM = 4
KNOCKOUT_ROUNDS = ("Quarterfinal", "Semifinal", "Final")  # a team's 5th, 6th, 7th Cup game


class CupError(ValueError):
    """The Cup games don't fit the tournament's structure; refuse rather than guess."""


def _rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    result = response["resultSets"][0]
    return [dict(zip(result["headers"], values, strict=True)) for values in result["rowSet"]]


def final_only(response: dict[str, Any]) -> dict[str, Any]:
    """A copy of the response with only the final's rows (SEASON_ID prefix 6)."""
    result = response["resultSets"][0]
    season_id = result["headers"].index("SEASON_ID")
    rows = [row for row in result["rowSet"] if row[season_id].startswith(FINAL_SEASON_ID_PREFIX)]
    return {**response, "resultSets": [{**result, "rowSet": rows}, *response["resultSets"][1:]]}


def cup_rounds(response: dict[str, Any]) -> dict[str, str]:
    """{game_id: round} for every Cup game in the response."""
    game_teams: dict[str, set[int]] = {}
    game_dates: dict[str, str] = {}
    final_ids: set[str] = set()
    for row in _rows(response):
        game_teams.setdefault(row["GAME_ID"], set()).add(row["TEAM_ID"])
        game_dates[row["GAME_ID"]] = row["GAME_DATE"]
        if row["SEASON_ID"].startswith(FINAL_SEASON_ID_PREFIX):
            final_ids.add(row["GAME_ID"])

    # Each team's Cup games in date order; the position decides the round.
    games_by_team: dict[int, list[str]] = {}
    for game_id in sorted(game_teams, key=lambda g: (game_dates[g], g)):
        for team_id in game_teams[game_id]:
            games_by_team.setdefault(team_id, []).append(game_id)

    rounds: dict[str, str] = {}
    for game_id, teams in game_teams.items():
        if len(teams) != 2:
            raise CupError(f"Cup game {game_id} has {len(teams)} teams, expected 2")
        names = {_round_for_position(games_by_team[team].index(game_id)) for team in teams}
        if len(names) != 1:
            raise CupError(f"Teams in Cup game {game_id} disagree about the round: {names}")
        (rounds[game_id],) = names

    finals = {game_id for game_id, name in rounds.items() if name == "Final"}
    if finals != final_ids:
        raise CupError(f"The 7th Cup game {finals} isn't the game marked as the final {final_ids}")
    return rounds


def _round_for_position(index: int) -> str:
    """0-based position in a team's Cup games -> round name."""
    if index < GROUP_GAMES_PER_TEAM:
        return "Group"
    knockout = index - GROUP_GAMES_PER_TEAM
    if knockout >= len(KNOCKOUT_ROUNDS):
        raise CupError(f"A team has more than {GROUP_GAMES_PER_TEAM + 3} Cup games")
    return KNOCKOUT_ROUNDS[knockout]
