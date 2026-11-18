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
