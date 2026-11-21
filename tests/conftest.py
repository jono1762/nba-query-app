"""Shared test fixtures.

Database tests need a Postgres server (locally: `docker compose up -d`) and the
DATABASE_URL environment variable. Each test gets its own brand-new, empty
database, which is deleted afterwards, so tests never see each other's data or
touch the development database. Without DATABASE_URL, database tests are skipped,
unless REQUIRE_DATABASE is set (as in CI), in which case they fail.
"""

import os
import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from nba_query_app.db.migrate import apply_migrations
from nba_query_app.ingest.normalize import OPTIONAL_STATS


@pytest.fixture
def server_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        if os.environ.get("REQUIRE_DATABASE"):
            pytest.fail("REQUIRE_DATABASE is set but DATABASE_URL is missing")
        pytest.skip("DATABASE_URL not set; skipping database test")
    return url


@pytest.fixture
def test_db_url(server_url: str) -> Iterator[str]:
    """Create a throwaway database and return a connection URL for it."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(server_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(server_url, dbname=name)
    finally:
        with psycopg.connect(server_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def db(test_db_url: str) -> Iterator[psycopg.Connection]:
    """An autocommit connection to the throwaway database, as the admin user."""
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        yield conn


@pytest.fixture
def connect_as(
    db: psycopg.Connection, test_db_url: str
) -> Iterator[Callable[[str], psycopg.Connection]]:
    """Returns a function that connects to the test database as a member of a role."""
    apply_migrations(db)
    created: list[tuple[str, psycopg.Connection]] = []

    def _connect(role: str) -> psycopg.Connection:
        user = f"test_{role}_{uuid.uuid4().hex[:8]}"
        password = uuid.uuid4().hex
        db.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE {}").format(
                sql.Identifier(user), sql.Literal(password), sql.Identifier(role)
            )
        )
        conn = psycopg.connect(make_conninfo(test_db_url, user=user, password=password))
        conn.autocommit = True
        created.append((user, conn))
        return conn

    yield _connect

    for user, conn in created:
        conn.close()
        db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(user)))


class NbaData:
    """Writes small, hand-made games straight into the tables, for testing SQL over them.

    Unspecified counting stats are recorded as 0 (attempts default to the makes,
    to satisfy the table's checks); pass None for "not recorded".
    Teams and players are created on first use.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self._games: dict[str, tuple[int, int, bool]] = {}  # game -> (home, away, neutral)

    def team(self, team_id: int, abbreviation: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO nba.teams VALUES (%s, %s, %s, '2000-01-01') ON CONFLICT DO NOTHING",
            (team_id, abbreviation or f"T{team_id}", f"Team {team_id}"),
        )

    def player(self, player_id: int, full_name: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO nba.players VALUES (%s, %s, '2000-01-01') ON CONFLICT DO NOTHING",
            (player_id, full_name or f"Player {player_id}"),
        )

    def game(
        self,
        game_id: str,
        season: str = "2000-01",
        season_type: str = "Regular Season",
        *,
        home: int = 1,
        away: int = 2,
        game_date: str | None = None,
        neutral_site: bool = False,
        nba_cup_round: str | None = None,
    ) -> None:
        self.team(home)
        self.team(away)
        day = game_date or f"{season[:4]}-12-01"
        self.conn.execute(
            "INSERT INTO nba.games (game_id, game_date, season, season_type, home_team_id, "
            "away_team_id, neutral_site, nba_cup_round) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                game_id,
                day,
                season,
                season_type,
                None if neutral_site else home,
                None if neutral_site else away,
                neutral_site,
                nba_cup_round,
            ),
        )
        self._games[game_id] = (home, away, neutral_site)

    def stats(
        self,
        game_id: str,
        player_id: int,
        team_id: int,
        *,
        won: bool,
        pts: int = 0,
        fgm: int = 0,
        ftm: int = 0,
        abbreviation: str | None = None,
        **optional: int | None,
    ) -> None:
        unknown = set(optional) - set(OPTIONAL_STATS)
        assert not unknown, f"unknown stats {unknown}"
        home, away, neutral = self._games[game_id]
        assert team_id in (home, away), f"team {team_id} isn't in game {game_id}"
        self.player(player_id)
        defaults = {"fga": fgm, "fg3a": optional.get("fg3m") or 0, "fta": ftm}
        values = {c: optional.get(c, defaults.get(c, 0)) for c in OPTIONAL_STATS}
        row = {
            "game_id": game_id,
            "player_id": player_id,
            "team_id": team_id,
            "team_abbreviation": abbreviation or f"T{team_id}",
            "opponent_team_id": away if team_id == home else home,
            "is_home": None if neutral else team_id == home,
            "won": won,
            "pts": pts,
            "fgm": fgm,
            "ftm": ftm,
            **values,
        }
        self.conn.execute(
            sql.SQL("INSERT INTO nba.player_game_stats ({}) VALUES ({})").format(
                sql.SQL(", ").join(map(sql.Identifier, row)),
                sql.SQL(", ").join(sql.Placeholder() * len(row)),
            ),
            list(row.values()),
        )

    def team_stats(
        self, game_id: str, team_id: int, *, pts: int, opponent_pts: int, **stats: int | None
    ) -> None:
        """A team-log row: final score (won is worked out from it), other stats optional."""
        home, away, neutral = self._games[game_id]
        assert team_id in (home, away), f"team {team_id} isn't in game {game_id}"
        row = {
            "game_id": game_id,
            "team_id": team_id,
            "team_abbreviation": f"T{team_id}",
            "opponent_team_id": away if team_id == home else home,
            "is_home": None if neutral else team_id == home,
            "won": pts > opponent_pts,
            "pts": pts,
            "opponent_pts": opponent_pts,
            **stats,
        }
        self.conn.execute(
            sql.SQL("INSERT INTO nba.team_game_stats ({}) VALUES ({})").format(
                sql.SQL(", ").join(map(sql.Identifier, row)),
                sql.SQL(", ").join(sql.Placeholder() * len(row)),
            ),
            list(row.values()),
        )


@pytest.fixture
def nba_data(db: psycopg.Connection) -> NbaData:
    """A migrated, empty test database plus a helper for adding hand-made games."""
    apply_migrations(db)
    return NbaData(db)
