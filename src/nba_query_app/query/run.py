"""Run AI-written SQL: safety-check it, then run it with the database's own guards on.

The safety checker (safety.py) is the first layer. The rest are enforced by
Postgres itself, so they hold even if the checker misses something:
- The connection must be a read-only user (a member of nba_reader). run_query
  refuses to start if the user can write to any nba table, so a configuration
  mistake (e.g. connecting as the admin) fails loudly instead of quietly
  running AI-written SQL with write access.
- The query runs in a READ ONLY transaction, so Postgres refuses any write.
- statement_timeout stops it after a few seconds.
- It runs through a server-side cursor (DECLARE ... CURSOR FOR <sql>). Postgres
  accepts only a single SELECT or VALUES there, with no data-modifying WITH,
  and the rows stay on the server: only max_rows + 1 are fetched, however many
  the query would return.
- search_path is set to nba for the transaction, so bare table names work.

Intervals (e.g. age(game_date, birth_date)) come back as Postgres writes them,
'18 years 11 mons 25 days', rather than as a Python timedelta, which only counts
days and would turn an age into "6,925 days".

The settings use SET LOCAL, which lasts only until the transaction ends, so the
connection is back to normal afterwards and can be reused.
"""

from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import pq
from psycopg.types.string import TextLoader

from nba_query_app.query.safety import check_sql

DEFAULT_TIMEOUT_MS = 5_000
DEFAULT_MAX_ROWS = 100


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool  # the query had more than max_rows rows; only the first max_rows are here


class QueryTimeoutError(Exception):
    """The query ran longer than the time limit and was stopped."""


class WriteAccessError(RuntimeError):
    """The connection's user can write, so it mustn't run AI-written SQL."""


def run_query(
    conn: psycopg.Connection,
    sql: str,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> QueryResult:
    """Safety-check `sql`, then run it read-only. Raises UnsafeSqlError if it's refused.

    `conn` must be a read-only user's connection, not inside a transaction.
    Database errors (e.g. a misspelt column) are raised as psycopg errors.
    """
    check_sql(sql)
    return run_read_only(conn, sql, timeout_ms=timeout_ms, max_rows=max_rows)


def run_read_only(
    conn: psycopg.Connection,
    sql: str,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> QueryResult:
    """Run `sql` with the database's guards on, WITHOUT the safety check. Use run_query.

    Kept separate so tests can show the database guards hold on their own.
    """
    if conn.info.transaction_status != pq.TransactionStatus.IDLE:
        raise ValueError("run_read_only needs a connection that isn't inside a transaction")
    try:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")  # must come first in the transaction
            # SET LOCAL can't take a query parameter; set_config(..., true) is the same thing.
            conn.execute("SELECT set_config('statement_timeout', %s, true)", (str(timeout_ms),))
            conn.execute("SET LOCAL search_path = nba")
            _refuse_write_access(conn)
            with conn.cursor(name="answer") as cur:
                cur.adapters.register_loader("interval", TextLoader)
                cur.execute(sql)
                rows = cur.fetchmany(max_rows + 1)
                columns = [column.name for column in cur.description or []]
    except psycopg.errors.QueryCanceled:
        raise QueryTimeoutError(
            f"The query took longer than {timeout_ms / 1000:g} seconds and was stopped."
        ) from None
    return QueryResult(columns, rows[:max_rows], truncated=len(rows) > max_rows)


def _refuse_write_access(conn: psycopg.Connection) -> None:
    row = conn.execute(
        "SELECT coalesce(bool_or(has_table_privilege(c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE')),"
        " false) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = 'nba'"
    ).fetchone()
    if row is None or row[0]:
        raise WriteAccessError(
            f"Refusing to run AI-written SQL as {conn.info.user!r}, which can write to the "
            "nba tables. Connect as a member of nba_reader."
        )
