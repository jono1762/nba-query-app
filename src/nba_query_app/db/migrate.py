"""Apply numbered SQL migration files to a database, in order, once each.

Migrations live in the top-level `migrations/` folder and are named like
`001_create_nba_tables.sql`. Each file runs inside its own transaction, so a
failing file leaves the database unchanged. Applied files are recorded in the
`public.schema_migrations` table and skipped on later runs.

Usage:
    uv run --env-file .env python -m nba_query_app.db.migrate
"""

import os
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"

CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """All .sql files in the folder, sorted by name (so 001 runs before 002)."""
    return sorted(directory.glob("*.sql"))


def apply_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply any migrations not yet recorded. Returns the names of the files applied."""
    with conn.transaction():
        conn.execute(CREATE_TRACKING_TABLE)
    applied = {row[0] for row in conn.execute("SELECT version FROM public.schema_migrations")}

    newly_applied = []
    for path in migration_files(directory):
        if path.name in applied:
            continue
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute("INSERT INTO public.schema_migrations (version) VALUES (%s)", (path.name,))
        newly_applied.append(path.name)
    return newly_applied


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as conn:
        newly_applied = apply_migrations(conn)
    if newly_applied:
        for name in newly_applied:
            print(f"Applied {name}")
    else:
        print("Database is up to date.")


if __name__ == "__main__":
    main()
