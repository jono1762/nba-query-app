"""Work out each playoff game's round and its game number within the series.

A series is every playoff game between the same two teams in a season, and its
winner is the team with more wins. The source has no reliable round labels for
old seasons (the round digit in old game IDs is often wrong), so rounds are
inferred from who played whom:

- Completed playoffs: count back from the Finals. The Finals is the one series
  whose winner plays no later series; the series its two teams won before it are
  one round earlier, and so on. Counting back handles byes (in many old seasons
  the top teams skipped the first round, so a team's first series wasn't always
  the league's first round) and uneven brackets (1949-50).
- Playoffs still being played (no Finals yet): count forward, each series one
  round after the latest series either team came from, with the current
  PLAYOFF_ROUNDS-round format.
- Anything that isn't a knockout bracket returns None rather than guess: a team
  playing two series at once, a loser playing on, or a tied pair playing on. The
  1953-54 round robin (three teams each playing the other two) is one.

This module does no network or database work.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import date

# Rounds in the current format (since 1983-84): first round, conference
# semifinals, conference finals, Finals.
PLAYOFF_ROUNDS = 4
# Divisions were the top-level groups until 1970-71, when conferences began.
FIRST_CONFERENCE_SEASON = "1970-71"


@dataclass(frozen=True)
class PlayoffGame:
    """What the inference needs from one team's row in one playoff game."""

    game_id: str
    game_date: date
    team_id: int
    won: bool


@dataclass(frozen=True)
class PlayoffRound:
    round: int  # 1 = the season's first round, up to the Finals
    round_name: str
    series_game: int  # game number within the series, 1 = first


@dataclass
class _Series:
    teams: frozenset[int]
    games: list[tuple[date, str]]  # (date, game_id), sorted
    wins: Counter[int]

    @property
    def start(self) -> date:
        return self.games[0][0]

    @property
    def end(self) -> date:
        return self.games[-1][0]

    @property
    def winner(self) -> int | None:
        (leader, most), *rest = self.wins.most_common() + [(None, 0)]
        return leader if rest and most > rest[0][1] else None


def round_name(rounds_before_finals: int, season: str) -> str:
    """0 -> 'Finals', 1 -> 'Conference Finals' (or 'Division Finals' before
    1970-71), 2 -> 'Conference Semifinals' (or 'Division Semifinals'), 3 -> 'First Round'."""
    group = "Conference" if season >= FIRST_CONFERENCE_SEASON else "Division"
    names = {0: "Finals", 1: f"{group} Finals", 2: f"{group} Semifinals", 3: "First Round"}
    if rounds_before_finals not in names:
        raise ValueError(f"No name for a round {rounds_before_finals} rounds before the Finals")
    return names[rounds_before_finals]


def playoff_rounds(season: str, rows: list[PlayoffGame]) -> dict[str, PlayoffRound] | None:
    """Round and series game number for every game in one season's playoffs.

    `rows` has each game's two team rows. Returns None if the games don't form a
    knockout bracket (see the module docstring).
    """
    series = _group_into_series(rows)
    if not series:
        return {}
    ordered = sorted(series, key=lambda s: (s.start, s.end))
    for i, s in enumerate(ordered):
        if any(s.teams & o.teams and o.start <= s.end for o in ordered[i + 1 :]):
            return None  # a team in two series at once: not a knockout bracket

    # Each series' next series: the winner's first series after it.
    next_of: dict[int, _Series | None] = {}
    for s in ordered:
        later = [o for o in ordered if o is not s and o.start >= s.end]
        if s.winner is None:
            if any(s.teams & o.teams for o in later):
                return None  # a tied pair that played on: not a knockout series
            next_of[id(s)] = None
            continue
        loser = next(iter(s.teams - {s.winner}))
        if any(loser in o.teams for o in later):
            return None  # the loser played again: not a knockout series
        following = [o for o in later if s.winner in o.teams]
        next_of[id(s)] = following[0] if following else None

    # Round counted forward: one after the latest series either team came from.
    forward: dict[int, int] = {}
    for s in ordered:
        before = [forward[id(o)] for o in ordered if id(o) in forward and o.teams & s.teams]
        forward[id(s)] = 1 + max(before, default=0)

    last = [s for s in ordered if next_of[id(s)] is None]
    if len(last) == 1 and last[0].winner is not None:
        # One series nobody advances from: the Finals. Count back from it.
        before_finals = {id(s): _rounds_before(s, next_of) for s in ordered}
        rounds = 1 + max(before_finals.values())
        numbering = {k: rounds - v for k, v in before_finals.items()}
    elif max(forward.values()) <= PLAYOFF_ROUNDS:
        # Still being played (no Finals yet): count forward.
        rounds = PLAYOFF_ROUNDS
        numbering = forward
    else:
        return None

    result = {}
    for s in ordered:
        name = round_name(rounds - numbering[id(s)], season)
        for number, (_, game_id) in enumerate(s.games, start=1):
            result[game_id] = PlayoffRound(numbering[id(s)], name, number)
    return result


def _group_into_series(rows: list[PlayoffGame]) -> list[_Series]:
    by_game: dict[str, list[PlayoffGame]] = {}
    for row in rows:
        by_game.setdefault(row.game_id, []).append(row)

    by_pair: dict[frozenset[int], _Series] = {}
    for game_id, teams in by_game.items():
        if len(teams) != 2:
            raise ValueError(f"Playoff game {game_id} has {len(teams)} team rows, expected 2")
        pair = frozenset(t.team_id for t in teams)
        s = by_pair.setdefault(pair, _Series(pair, [], Counter()))
        s.games.append((teams[0].game_date, game_id))
        s.wins.update(t.team_id for t in teams if t.won)
    for s in by_pair.values():
        s.games.sort()
    return list(by_pair.values())


def _rounds_before(s: _Series, next_of: dict[int, "_Series | None"]) -> int:
    count = 0
    while (s := next_of[id(s)]) is not None:
        count += 1
    return count
