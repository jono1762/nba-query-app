"""Proves the database itself enforces permissions, independent of any app code.

Each test creates a temporary login user that is a member of one role, connects
as that user, and checks what it can and cannot do.
"""

from collections.abc import Callable

import psycopg
import pytest
from psycopg import sql

ConnectAs = Callable[[str], psycopg.Connection]


@pytest.mark.parametrize(
    "table", ["teams", "players", "games", "player_game_stats", "meta", "player_season_stats"]
)
def test_reader_can_select_every_table(connect_as: ConnectAs, table: str) -> None:
    reader = connect_as("nba_reader")
    reader.execute(sql.SQL("SELECT * FROM nba.{}").format(sql.Identifier(table)))


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO nba.teams VALUES (99, 'XXX', 'Fake Team', '2026-10-20')",
        "UPDATE nba.meta SET data_version = 999",
        "DELETE FROM nba.meta",
        "TRUNCATE nba.meta",
        "DROP TABLE nba.meta",
        "ALTER TABLE nba.meta ADD COLUMN hacked int",
        "CREATE TABLE nba.hacked (x int)",
        "CREATE TABLE public.hacked (x int)",
        "CREATE TEMP TABLE hacked (x int)",
        "CREATE SCHEMA hacked",
    ],
)
def test_reader_cannot_write_anything(connect_as: ConnectAs, statement: str) -> None:
    reader = connect_as("nba_reader")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reader.execute(statement)


def test_reader_cannot_read_migration_tracking(connect_as: ConnectAs) -> None:
    reader = connect_as("nba_reader")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reader.execute("SELECT * FROM public.schema_migrations")


def test_ingest_can_write_rows(connect_as: ConnectAs) -> None:
    ingest = connect_as("nba_ingest")
    ingest.execute("INSERT INTO nba.teams VALUES (1, 'OKC', 'Oklahoma City Thunder', '2026-10-20')")
    ingest.execute("UPDATE nba.meta SET data_version = data_version + 1")
    assert ingest.execute("SELECT data_version FROM nba.meta").fetchone() == (1,)


@pytest.mark.parametrize(
    "statement",
    ["DROP TABLE nba.meta", "CREATE TABLE nba.hacked (x int)", "TRUNCATE nba.meta"],
)
def test_ingest_cannot_change_structure(connect_as: ConnectAs, statement: str) -> None:
    ingest = connect_as("nba_ingest")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ingest.execute(statement)
