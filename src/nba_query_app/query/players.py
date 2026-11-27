"""Find the players a question names, so the AI is told their exact IDs.

The AI can't see the stored names, so without this it guesses how a name is
stored, and guesses wrong for players it doesn't know (new players) or names typed
in lowercase. So before asking the AI, the question is matched against every
player's name, ignoring case, punctuation and any accents typed (stored names have
none, see names.py), and the matches (with player_id and the seasons they played)
go into the prompt. A name shared by several players (there
are two Eddie Johnsons) returns all of them, so the AI can cover each or say which.

Matching:
- a full name ("Nikola Jokic", "shaquille o'neal") anywhere in the question;
- otherwise a capitalised word that is a player's last name ("Jokic's"), if at
  most MAX_SURNAME_MATCHES players have it, so "James" alone doesn't list dozens,
  and if it isn't part of a team name ("Boston Celtics" isn't Brandon Boston).
Nicknames ("Shaq") aren't matched; the AI knows those well enough to write the
full name.
"""

import re
from dataclasses import dataclass

import psycopg

from nba_query_app.names import strip_accents

# A last name on its own is used only if this few players have it.
MAX_SURNAME_MATCHES = 5


@dataclass(frozen=True)
class PlayerMatch:
    mentioned_as: str  # the words in the question
    player_id: int
    full_name: str
    first_season: str | None = None
    last_season: str | None = None


def normalize(text: str) -> list[str]:
    """Lowercase words, accents and punctuation removed, apostrophes closed up:
    "Dončić O'Neal Abdul-Jabbar" -> ['doncic', 'oneal', 'abdul', 'jabbar']."""
    plain = re.sub(r"'", "", strip_accents(text).lower())
    return re.sub(r"[^a-z0-9]+", " ", plain).split()


class PlayerNames:
    """Every player's name, normalized once, for matching against questions."""

    def __init__(self, players: list[tuple[int, str]], team_names: list[str] = ()) -> None:
        self._by_full_name: dict[tuple[str, ...], list[tuple[int, str]]] = {}
        self._by_surname: dict[str, list[tuple[int, str]]] = {}
        for player_id, full_name in players:
            words = tuple(normalize(full_name))
            if not words:
                continue
            self._by_full_name.setdefault(words, []).append((player_id, full_name))
            if len(words) > 1:
                self._by_surname.setdefault(words[-1], []).append((player_id, full_name))
        self._longest = max((len(k) for k in self._by_full_name), default=0)
        self._team_words = {w for name in team_names for w in normalize(name)}

    @classmethod
    def load(cls, conn: psycopg.Connection) -> "PlayerNames":
        players = conn.execute("SELECT player_id, full_name FROM nba.players").fetchall()
        teams = [name for (name,) in conn.execute("SELECT name FROM nba.teams")]
        conn.rollback()  # leave no transaction open (run_read_only needs that)
        return cls(players, teams)

    def find(self, question: str) -> list[PlayerMatch]:
        original = re.findall(r"[^\W_]+(?:['’][^\W_]+)*", question)
        # A possessive 's is dropped: "Jokic's" -> 'jokic', "O'Neal's" -> 'oneal'.
        words = [normalize(re.sub(r"['’]s$", "", w)) for w in original]
        flat: list[str] = []
        owner: list[int] = []  # flat word -> index into original
        for i, parts in enumerate(words):
            flat.extend(parts)
            owner.extend([i] * len(parts))

        matches: list[PlayerMatch] = []
        used = [False] * len(flat)
        # Full names, longest first, so "Kareem Abdul Jabbar" wins over a shorter one.
        for length in range(self._longest, 1, -1):
            for start in range(len(flat) - length + 1):
                if any(used[start : start + length]):
                    continue
                players = self._by_full_name.get(tuple(flat[start : start + length]))
                if players:
                    said = " ".join(original[owner[start] : owner[start + length - 1] + 1])
                    matches += [PlayerMatch(said, pid, name) for pid, name in players]
                    used[start : start + length] = [True] * length

        # Then capitalised last names on their own.
        for start, word in enumerate(flat):
            said = original[owner[start]]
            if used[start] or not said[:1].isupper() or word in self._team_words:
                continue
            players = self._by_surname.get(word, [])
            if 0 < len(players) <= MAX_SURNAME_MATCHES:
                matches += [PlayerMatch(said, pid, name) for pid, name in players]
                used[start] = True
        return matches


def with_seasons(conn: psycopg.Connection, matches: list[PlayerMatch]) -> list[PlayerMatch]:
    """Add each match's first and last season, which helps the AI tell namesakes apart."""
    if not matches:
        return []
    rows = conn.execute(
        "SELECT s.player_id, min(g.season), max(g.season) FROM nba.player_game_stats s "
        "JOIN nba.games g USING (game_id) WHERE s.player_id = ANY(%s) GROUP BY s.player_id",
        ([m.player_id for m in matches],),
    ).fetchall()
    conn.rollback()  # leave no transaction open (run_read_only needs that)
    seasons = {pid: (first, last) for pid, first, last in rows}
    return [
        PlayerMatch(
            m.mentioned_as, m.player_id, m.full_name, *seasons.get(m.player_id, (None, None))
        )
        for m in matches
    ]
