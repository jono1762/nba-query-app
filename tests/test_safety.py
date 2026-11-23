"""The SQL safety checker: realistic queries pass, and attempts to break out are refused."""

import psycopg
import pytest

from nba_query_app.db.migrate import apply_migrations
from nba_query_app.query.safety import (
    ALLOWED_SCHEMA,
    ALLOWED_TABLES,
    MAX_SQL_LENGTH,
    UnsafeSqlError,
    check_sql,
)

# Queries like the ones the AI should write. All must pass.
REALISTIC = [
    # Top scorers in a month
    "SELECT p.full_name, SUM(s.pts) AS points FROM nba.player_game_stats s "
    "JOIN nba.players p USING (player_id) JOIN nba.games g USING (game_id) "
    "WHERE g.game_date >= DATE '2025-03-01' AND g.game_date < DATE '2025-04-01' "
    "AND g.season_type = 'Regular Season' GROUP BY p.full_name ORDER BY points DESC LIMIT 10",
    # Averages, filters, HAVING
    "SELECT full_name, ROUND(AVG(pts)::numeric, 1), COUNT(*) FILTER (WHERE pts >= 30) "
    "FROM nba.player_game_stats JOIN nba.players USING (player_id) "
    "GROUP BY 1 HAVING COUNT(*) > 10",
    # Streaks with window functions (gaps and islands), via WITH
    "WITH g AS (SELECT player_id, game_date, ROW_NUMBER() OVER (PARTITION BY player_id "
    "ORDER BY game_date) - ROW_NUMBER() OVER (PARTITION BY player_id, pts >= 30 "
    "ORDER BY game_date) AS grp FROM nba.player_game_stats JOIN nba.games USING (game_id)) "
    "SELECT player_id, COUNT(*) FROM g GROUP BY player_id, grp ORDER BY 2 DESC LIMIT 5",
    # Ages and dates
    "SELECT EXTRACT(YEAR FROM age(g.game_date, d.birth_date)), "
    "date_part('year', age(g.game_date, d.birth_date)), date_trunc('month', g.game_date), "
    "to_char(g.game_date, 'YYYY-MM'), make_date(2020, 1, 1), CURRENT_DATE, now(), "
    "g.game_date - INTERVAL '1 year' FROM nba.games g, nba.player_details d",
    # Conditions and arithmetic
    "SELECT CASE WHEN pts >= 10 AND reb >= 10 AND ast >= 10 THEN 'triple-double' "
    "WHEN pts > 5 OR reb > 5 THEN 'x' END, COALESCE(fg3m, 0), fgm::float / NULLIF(fga, 0), "
    "GREATEST(pts, reb), LEAST(pts, reb), ABS(plus_minus), pts % 2, "
    "round(100.0 * fgm / nullif(fga, 0), 1) FROM nba.player_game_stats "
    "WHERE NOT is_home AND won IS NOT TRUE AND fg3m IS NULL AND pts BETWEEN 1 AND 2",
    # Window and statistical functions
    "SELECT RANK() OVER w, DENSE_RANK() OVER w, LAG(pts) OVER w, LEAD(pts, 1) OVER w, "
    "FIRST_VALUE(pts) OVER w, STDDEV(pts) OVER w, "
    "SUM(pts) OVER (ORDER BY player_id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) "
    "FROM nba.player_game_stats WINDOW w AS (ORDER BY pts)",
    "SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pts), MODE() WITHIN GROUP "
    "(ORDER BY pts), STRING_AGG(team_abbreviation, ', ' ORDER BY team_abbreviation), "
    "BOOL_OR(won), BOOL_AND(won), COUNT(DISTINCT team_id), ARRAY_AGG(game_id) "
    "FROM nba.player_game_stats",
    # Text
    "SELECT LOWER(full_name), UPPER(full_name), LENGTH(full_name), TRIM(full_name), "
    "SPLIT_PART(full_name, ' ', 2), SUBSTRING(full_name FROM 1 FOR 3), "
    "CONCAT(full_name, '!'), full_name || '!', REPLACE(full_name, 'a', 'b'), "
    "POSITION('a' IN full_name), LEFT(full_name, 1), INITCAP(full_name) FROM nba.players "
    "WHERE full_name ILIKE '%james%' OR full_name ~* '^lebron' OR full_name LIKE 'L%'",
    # Sets, subqueries, ANY, EXISTS
    "SELECT * FROM nba.playoff_series WHERE season = '2015-16' AND playoff_round_name = 'Finals' "
    "UNION ALL SELECT * FROM nba.playoff_series WHERE EXISTS (SELECT 1 FROM nba.games) "
    "AND season = ANY(ARRAY['2014-15', '2016-17']) "
    "AND winner_team_id NOT IN (SELECT team_id FROM nba.teams WHERE abbreviation = 'GSW')",
    "SELECT DISTINCT ON (player_id) player_id, pts FROM nba.player_game_stats "
    "ORDER BY player_id, pts DESC NULLS LAST",
    # Bare table names (the runner sets search_path to nba), every table and view
    *[f"SELECT * FROM {table}" for table in sorted(ALLOWED_TABLES)],
    # Case-insensitive names, a trailing semicolon, comments
    "select * from NBA.Players;",
    "-- the newest game\nSELECT latest_game_date FROM nba.meta /* one row */",
    "SELECT 1",
]


@pytest.mark.parametrize("sql", REALISTIC)
def test_realistic_queries_pass(sql: str) -> None:
    check_sql(sql)


# (attack, part of the expected reason)
ATTACKS = [
    # Writes, top level
    ("INSERT INTO nba.teams VALUES (99, 'X', 'X', '2026-01-01')", "Only SELECT"),
    ("UPDATE nba.meta SET data_version = 0", "Only SELECT"),
    ("DELETE FROM nba.meta", "Only SELECT"),
    ("MERGE INTO nba.teams t USING nba.teams s ON true WHEN MATCHED THEN DELETE", "Only SELECT"),
    ("TRUNCATE nba.meta", "Only SELECT"),
    ("DROP TABLE nba.meta", "Only SELECT"),
    ("ALTER TABLE nba.meta ADD COLUMN x int", "Only SELECT"),
    ("CREATE TABLE nba.x AS SELECT 1", "Only SELECT"),
    ("GRANT ALL ON nba.meta TO PUBLIC", "Only SELECT"),
    ("COPY nba.players TO '/tmp/players.csv'", "Only SELECT"),
    ("VALUES (1)", "Only SELECT"),
    # Writes hidden inside a query
    ("WITH d AS (DELETE FROM nba.meta RETURNING *) SELECT * FROM d", "DELETE isn't allowed"),
    (
        "WITH u AS (UPDATE nba.meta SET data_version = 0 RETURNING 1) SELECT * FROM u",
        "UPDATE isn't allowed",
    ),
    (
        "SELECT * FROM (SELECT 1) x WHERE EXISTS "
        "(WITH i AS (INSERT INTO nba.teams VALUES (1, 'a', 'b', now()) RETURNING 1) "
        "SELECT 1 FROM i)",
        "INSERT isn't allowed",
    ),
    ("SELECT * INTO nba.copy FROM nba.players", "INTO isn't allowed"),
    ("SELECT * FROM nba.meta FOR UPDATE", "LOCK isn't allowed"),
    ("SELECT * FROM nba.meta FOR SHARE", "LOCK isn't allowed"),
    # Sneaking in a second statement
    ("SELECT 1; DROP TABLE nba.meta", "exactly one statement"),
    ("SELECT 1; SELECT 2", "exactly one statement"),
    ("SELECT 1; SET statement_timeout = 0", "exactly one statement"),
    ("SELECT 1;; DELETE FROM nba.meta", "exactly one statement"),
    ("SELECT 'a'; DELETE FROM nba.meta; --'", "exactly one statement"),
    # Session and server commands
    ("SET statement_timeout = 0", "Only SELECT"),
    ("SET ROLE postgres", "Only SELECT"),
    ("RESET ROLE", "Only SELECT"),
    ("SHOW ALL", "Only SELECT"),
    ("BEGIN", "Only SELECT"),
    ("COMMIT", "Only SELECT"),
    ("VACUUM", "Only SELECT"),
    ("EXPLAIN ANALYZE SELECT 1", "Only SELECT"),
    ("LISTEN x", "Only SELECT"),
    ("DO $$ BEGIN PERFORM pg_sleep(10); END $$", "Only SELECT"),
    ("PREPARE p AS SELECT 1", "Only SELECT"),
    ("CALL nba.f()", "Only SELECT"),
    # System tables and other schemas
    ("SELECT * FROM pg_roles", "Table pg_roles"),
    ("SELECT * FROM pg_catalog.pg_settings", "Table pg_catalog.pg_settings"),
    ("SELECT * FROM PG_CATALOG.PG_USER", "Table pg_catalog.pg_user"),
    ("SELECT * FROM information_schema.tables", "Table information_schema.tables"),
    ("SELECT * FROM public.schema_migrations", "Table public.schema_migrations"),
    ("SELECT * FROM schema_migrations", "Table schema_migrations"),
    ("SELECT * FROM nba.nba.players", "isn't allowed"),
    ("SELECT * FROM otherdb.nba.players", "isn't allowed"),
    ("SELECT * FROM nba.pg_class", "Table nba.pg_class"),
    ('SELECT * FROM "Players"', "Table Players"),
    ("SELECT * FROM nba.players p JOIN pg_stat_activity a ON true", "pg_stat_activity"),
    ("SELECT (SELECT count(*) FROM pg_class)", "Table pg_class"),
    ("SELECT * FROM nba.players WHERE player_id IN (SELECT oid FROM pg_proc)", "pg_proc"),
    ("SELECT 1 UNION SELECT usename::int FROM pg_user", "Table pg_user"),
    # WITH names can't hide system tables
    ("WITH pg_roles AS (SELECT 1) SELECT * FROM pg_roles", "can't start with pg_"),
    ('WITH "pg_roles" AS (SELECT 1) SELECT * FROM pg_roles', "can't start with pg_"),
    (
        "SELECT * FROM (WITH x AS (SELECT 1) SELECT * FROM x) a, nba.x",
        "Table nba.x",
    ),
    ("WITH x AS (SELECT * FROM pg_shadow) SELECT * FROM x", "Table pg_shadow"),
    # Casts that look up system objects
    ("SELECT 'pg_roles'::regclass", "Casting to REGCLASS"),
    ("SELECT 'pg_sleep'::regproc", "Casting to REGPROC"),
    ("SELECT CAST('nba_reader' AS regrole)", "Casting to REGROLE"),
    # Slow-down tricks
    ("SELECT pg_sleep(10)", "Function pg_sleep"),
    ("SELECT PG_SLEEP(10)", "Function PG_SLEEP"),
    ('SELECT "pg_sleep"(10)', "Function pg_sleep"),
    ("SELECT pg_sleep_for('10 seconds')", "Function pg_sleep_for"),
    ("SELECT pg_catalog.pg_sleep(10)", "Schema-qualified"),
    ("SELECT pg_catalog.age(now())", "Schema-qualified"),
    ("SELECT * FROM nba.players WHERE pg_sleep(10) IS NULL", "Function pg_sleep"),
    ("SELECT count(*) FROM generate_series(1, 1000000000)", "Function GENERATE_SERIES"),
    ("SELECT generate_series(1, 1000000000)", "Function GENERATE_SERIES"),
    ("SELECT * FROM ROWS FROM (pg_sleep(10))", "Function pg_sleep"),
    ("SELECT * FROM LATERAL generate_series(1, 10)", "Function GENERATE_SERIES"),
    ("SELECT repeat('x', 1000000000)", "Function REPEAT"),
    ("SELECT lpad('x', 1000000000)", "Function LPAD"),
    (
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT * FROM r",
        "WITH RECURSIVE",
    ),
    ("SELECT 1" + " " * MAX_SQL_LENGTH, "longer than"),
    ("SELECT " + "(" * 1000 + "1" + ")" * 1000, "nested too deeply"),
    # Functions that read the server, change settings or run hidden SQL
    ("SELECT set_config('statement_timeout', '0', false)", "Function set_config"),
    ("SELECT current_setting('data_directory')", "Function current_setting"),
    ("SELECT version()", "Function VERSION"),
    ("SELECT current_user", "Function CURRENT_USER"),
    ("SELECT query_to_xml('DELETE FROM nba.meta RETURNING 1', true, true, '')", "query_to_xml"),
    ("SELECT * FROM dblink('host=evil', 'SELECT 1') AS t(x int)", "Function dblink"),
    ("SELECT pg_read_file('/etc/passwd')", "Function pg_read_file"),
    ("SELECT lo_import('/etc/passwd')", "Function lo_import"),
    ("SELECT pg_terminate_backend(1)", "Function pg_terminate_backend"),
    ("SELECT pg_advisory_lock(1)", "Function pg_advisory_lock"),
    ("SELECT nextval('x')", "Function nextval"),
    ("SELECT txid_current()", "Function txid_current"),
    ("SELECT pg_notify('x', 'y')", "Function pg_notify"),
    ("SELECT nba.anything(1)", "Schema-qualified"),
    ("SELECT * FROM xmltable('/a' PASSING '<a/>' COLUMNS x int)", "isn't allowed"),
    # Junk
    ("", "exactly one statement"),
    ("   ", "exactly one statement"),
    ("-- just a comment", "exactly one statement"),
    ("SELECT FROM WHERE (", "couldn't be parsed"),
    ("SELECT * FROM nba.players WHERE full_name = 'unterminated", "couldn't be parsed"),
]


@pytest.mark.parametrize(("sql", "reason"), ATTACKS)
def test_attacks_are_refused(sql: str, reason: str) -> None:
    with pytest.raises(UnsafeSqlError) as caught:
        check_sql(sql)
    assert reason in str(caught.value)


def test_allowed_tables_match_the_database(db: psycopg.Connection) -> None:
    """ALLOWED_TABLES must list exactly the nba schema's tables and views."""
    apply_migrations(db)
    rows = db.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
        (ALLOWED_SCHEMA,),
    ).fetchall()
    assert {name for (name,) in rows} == ALLOWED_TABLES
