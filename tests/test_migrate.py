from pathlib import Path

import psycopg
import pytest

from nba_query.db.migrate import apply_migrations


def write(directory: Path, name: str, content: str) -> None:
    (directory / name).write_text(content)


def test_applies_files_in_order_and_records_them(db: psycopg.Connection, tmp_path: Path) -> None:
    write(tmp_path, "002_add_row.sql", "INSERT INTO t VALUES (1);")
    write(tmp_path, "001_create.sql", "CREATE TABLE t (x int);")

    assert apply_migrations(db, tmp_path) == ["001_create.sql", "002_add_row.sql"]
    assert db.execute("SELECT x FROM t").fetchall() == [(1,)]


def test_second_run_applies_nothing(db: psycopg.Connection, tmp_path: Path) -> None:
    write(tmp_path, "001_create.sql", "CREATE TABLE t (x int);")
    apply_migrations(db, tmp_path)

    assert apply_migrations(db, tmp_path) == []


def test_failed_migration_is_rolled_back_and_not_recorded(
    db: psycopg.Connection, tmp_path: Path
) -> None:
    write(tmp_path, "001_broken.sql", "CREATE TABLE t (x int); SELECT not_a_column FROM t;")

    with pytest.raises(psycopg.errors.UndefinedColumn):
        apply_migrations(db, tmp_path)

    # The CREATE TABLE in the same file was undone, and the file can be retried.
    assert db.execute("SELECT to_regclass('t')").fetchone() == (None,)
    assert db.execute("SELECT count(*) FROM public.schema_migrations").fetchone() == (0,)
