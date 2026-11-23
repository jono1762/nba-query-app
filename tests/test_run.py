"""Running AI-written SQL: results, limits, and the database's own guards.

The defence-in-depth tests call run_read_only, which skips the safety checker, to
show that Postgres refuses these attacks on its own, should the checker ever
miss one.
"""

from collections.abc import Callable

import psycopg
import pytest
from conftest import NbaData
from test_safety import REALISTIC

from nba_query_app.query.run import (
    QueryTimeoutError,
    WriteAccessError,
    run_query,
    run_read_only,
)
from nba_query_app.query.safety import UnsafeSqlError

ConnectAs = Callable[[str], psycopg.Connection]


@pytest.fixture
def reader(connect_as: ConnectAs, nba_data: NbaData) -> psycopg.Connection:
    """A read-only user's connection to a database holding one game."""
    nba_data.game("0020000001", home=1, away=2)
    for player_id in range(1, 6):
        nba_data.stats("0020000001", player_id, 1, won=True, pts=10 * player_id, fgm=player_id)
    return connect_as("nba_reader")


@pytest.mark.parametrize("sql", REALISTIC)
def test_realistic_queries_run_as_the_reader(reader: psycopg.Connection, sql: str) -> None:
    """Everything the checker lets through as realistic is valid SQL the reader may run."""
    run_query(reader, sql)


def test_returns_columns_and_rows(reader: psycopg.Connection) -> None:
    result = run_query(
        reader, "SELECT player_id, pts FROM player_game_stats WHERE pts >= 40 ORDER BY pts"
    )
    assert result.columns == ["player_id", "pts"]
    assert result.rows == [(4, 40), (5, 50)]
    assert not result.truncated


def test_percent_signs_are_not_placeholders(reader: psycopg.Connection) -> None:
    result = run_query(reader, "SELECT 7 % 4, 'a%b' LIKE 'a%'")
    assert result.rows == [(3, True)]


def test_rows_are_capped(reader: psycopg.Connection) -> None:
    sql = "SELECT player_id FROM nba.player_game_stats ORDER BY player_id"
    capped = run_query(reader, sql, max_rows=3)
    assert capped.rows == [(1,), (2,), (3,)]
    assert capped.truncated
    exact = run_query(reader, sql, max_rows=5)
    assert len(exact.rows) == 5
    assert not exact.truncated


def test_unsafe_sql_is_refused_before_running(reader: psycopg.Connection) -> None:
    with pytest.raises(UnsafeSqlError):
        run_query(reader, "SELECT * FROM pg_roles")


def test_database_errors_are_raised(reader: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.UndefinedColumn):
        run_query(reader, "SELECT no_such_column FROM nba.players")


def test_settings_end_with_the_query(reader: psycopg.Connection) -> None:
    """The time limit, read-only mode and search path don't stick to the connection."""
    before = reader.execute(
        "SELECT current_setting('statement_timeout'), current_setting('search_path')"
    ).fetchone()
    run_query(reader, "SELECT 1", timeout_ms=1234)
    with pytest.raises(psycopg.errors.UndefinedColumn):
        run_query(reader, "SELECT nope FROM nba.players")  # a failed query cleans up too
    after = reader.execute(
        "SELECT current_setting('statement_timeout'), current_setting('search_path'), "
        "current_setting('transaction_read_only')"
    ).fetchone()
    assert after == (*before, "off")


def test_refuses_to_run_as_a_user_who_can_write(
    db: psycopg.Connection, connect_as: ConnectAs
) -> None:
    with pytest.raises(WriteAccessError):
        run_query(db, "SELECT 1")  # the admin
    with pytest.raises(WriteAccessError):
        run_query(connect_as("nba_ingest"), "SELECT 1")


def test_refuses_a_connection_inside_a_transaction(reader: psycopg.Connection) -> None:
    with reader.transaction(), pytest.raises(ValueError, match="inside a transaction"):
        run_query(reader, "SELECT 1")


def test_slow_query_is_stopped(reader: psycopg.Connection) -> None:
    # A cross join that passes the checker but takes far longer than the limit.
    sql = (
        "SELECT count(*) FROM player_game_stats a, player_game_stats b, player_game_stats c, "
        "player_game_stats d, player_game_stats e, player_game_stats f, player_game_stats g, "
        "player_game_stats h, player_game_stats i, player_game_stats j, player_game_stats k"
    )
    with pytest.raises(QueryTimeoutError, match="0.2 seconds"):
        run_query(reader, sql, timeout_ms=200)


# --- Defence in depth: the database alone refuses these (safety checker skipped) ---


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM nba.meta",
        "UPDATE nba.meta SET data_version = 0",
        "WITH d AS (DELETE FROM nba.meta RETURNING *) SELECT * FROM d",
        "SELECT 1; DELETE FROM nba.meta",
        "SELECT * INTO nba.copy FROM nba.players",
        "SELECT * FROM nba.meta FOR UPDATE",
        "SET statement_timeout = 0",
        "COMMIT",
        "SELECT * FROM public.schema_migrations",
    ],
)
def test_database_refuses_writes_and_other_statements(reader: psycopg.Connection, sql: str) -> None:
    with pytest.raises(psycopg.Error):
        run_read_only(reader, sql)
    assert reader.execute("SELECT data_version FROM nba.meta").fetchone() == (0,)


def test_database_stops_pg_sleep(reader: psycopg.Connection) -> None:
    with pytest.raises(QueryTimeoutError):
        run_read_only(reader, "SELECT pg_sleep(10)", timeout_ms=200)


def test_query_cannot_switch_off_its_own_time_limit(reader: psycopg.Connection) -> None:
    sql = "SELECT set_config('statement_timeout', '0', false), pg_sleep(3)"
    with pytest.raises(QueryTimeoutError):
        run_read_only(reader, sql, timeout_ms=200)
