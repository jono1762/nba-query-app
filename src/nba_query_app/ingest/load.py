"""Write a normalized batch into the nba tables.

Every write is an "upsert": insert the row, or update it if it already exists.
Rows whose values haven't changed are left alone, so loading the same data twice
changes nothing. That makes re-running a download always safe.

Everything happens in one transaction: either the whole batch is saved or none of it.
"""

from dataclasses import astuple, dataclass, fields

import psycopg
from psycopg import sql

from nba_query_app.ingest.normalize import (
    Batch,
    Game,
    Player,
    PlayerGameStats,
    Team,
    TeamGameStats,
)


@dataclass(frozen=True)
class LoadResult:
    """How many rows were inserted or actually changed in each table."""

    teams: int
    players: int
    games: int
    stats: int
    team_stats: int

    @property
    def total(self) -> int:
        return self.teams + self.players + self.games + self.stats + self.team_stats


def _upsert_sql(table: str, row_type: type, key: tuple[str, ...], only_if_newer: bool) -> sql.SQL:
    """Build INSERT ... ON CONFLICT DO UPDATE for a dataclass whose fields match the columns.

    The update only happens when some value differs (and, for names, when the
    incoming row was seen on the same date or later).
    """
    columns = [f.name for f in fields(row_type)]
    updated = [c for c in columns if c not in key]
    target = sql.Identifier("nba", table)

    condition = sql.SQL("({}) IS DISTINCT FROM ({})").format(
        sql.SQL(", ").join(sql.SQL("{}.{}").format(target, sql.Identifier(c)) for c in updated),
        sql.SQL(", ").join(sql.SQL("EXCLUDED.{}").format(sql.Identifier(c)) for c in updated),
    )
    if only_if_newer:
        condition = sql.SQL("{} AND EXCLUDED.name_as_of >= {}.name_as_of").format(condition, target)

    return sql.SQL(
        "INSERT INTO {target} ({columns}) VALUES ({values}) "
        "ON CONFLICT ({key}) DO UPDATE SET {assignments} WHERE {condition}"
    ).format(
        target=target,
        columns=sql.SQL(", ").join(map(sql.Identifier, columns)),
        values=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
        key=sql.SQL(", ").join(map(sql.Identifier, key)),
        assignments=sql.SQL(", ").join(
            sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in updated
        ),
        condition=condition,
    )


UPSERT_TEAMS = _upsert_sql("teams", Team, ("team_id",), only_if_newer=True)
UPSERT_PLAYERS = _upsert_sql("players", Player, ("player_id",), only_if_newer=True)
UPSERT_GAMES = _upsert_sql("games", Game, ("game_id",), only_if_newer=False)
UPSERT_STATS = _upsert_sql(
    "player_game_stats", PlayerGameStats, ("game_id", "player_id", "team_id"), only_if_newer=False
)
UPSERT_TEAM_STATS = _upsert_sql(
    "team_game_stats", TeamGameStats, ("game_id", "team_id"), only_if_newer=False
)

# Bump the version only when something changed, so caches are cleared only when needed.
BUMP_META = """
UPDATE nba.meta
SET data_version = data_version + 1,
    latest_game_date = GREATEST(latest_game_date, %s),
    updated_at = now()
"""


def _upsert(cur: psycopg.Cursor, statement: sql.SQL, rows: list) -> int:
    if not rows:
        return 0
    cur.executemany(statement, [astuple(row) for row in rows])
    return cur.rowcount


def load_batch(conn: psycopg.Connection, batch: Batch) -> LoadResult:
    """Upsert the batch in one transaction. Returns counts of rows inserted or changed."""
    with conn.transaction(), conn.cursor() as cur:
        # Order matters: games and stats point at teams and players.
        result = LoadResult(
            teams=_upsert(cur, UPSERT_TEAMS, batch.teams),
            players=_upsert(cur, UPSERT_PLAYERS, batch.players),
            games=_upsert(cur, UPSERT_GAMES, batch.games),
            stats=_upsert(cur, UPSERT_STATS, batch.stats),
            team_stats=_upsert(cur, UPSERT_TEAM_STATS, batch.team_stats),
        )
        if result.total:
            latest = max((game.game_date for game in batch.games), default=None)
            cur.execute(BUMP_META, (latest,))
    return result


SET_CUP_ROUND = """
UPDATE nba.games SET nba_cup_round = %s
WHERE game_id = %s AND nba_cup_round IS DISTINCT FROM %s
"""


def load_cup_rounds(conn: psycopg.Connection, rounds: dict[str, str]) -> int:
    """Tag stored games with their NBA Cup round. Returns how many games changed.

    The games themselves must already be loaded: group, quarterfinal and semifinal
    games come from the regular season, the final from the NBA Cup Final load.
    """
    if not rounds:
        return 0
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT game_id FROM nba.games WHERE game_id = ANY(%s)", (list(rounds),))
        missing = set(rounds) - {game_id for (game_id,) in cur.fetchall()}
        if missing:
            raise ValueError(
                f"{len(missing)} Cup game(s) aren't loaded yet, e.g. {sorted(missing)[0]}; "
                "load the regular season and the NBA Cup final first"
            )
        cur.executemany(SET_CUP_ROUND, [(name, game_id, name) for game_id, name in rounds.items()])
        changed = cur.rowcount
        if changed:
            cur.execute(BUMP_META, (None,))
    return changed
