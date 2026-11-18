-- Permission roles. These are NOLOGIN "group" roles: nobody connects as them
-- directly. Each environment (laptop, CI, Neon) creates its own login users with
-- their own passwords and grants them one of these roles, so no passwords live in
-- migrations.
--
--   nba_reader  read-only access to the nba schema. LLM-written SQL runs ONLY as this.
--   nba_ingest  can load data into the nba schema (history download, daily update).
--
-- Note: roles are shared by the whole Postgres server, not one database, hence
-- the "create only if missing" checks.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nba_reader') THEN
        CREATE ROLE nba_reader NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nba_ingest') THEN
        CREATE ROLE nba_ingest NOLOGIN;
    END IF;
END
$$;

-- By default every user may create temporary tables. Take that away so the
-- reader truly cannot write anything, even scratch tables.
DO $$
BEGIN
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
END
$$;

-- Reader: look, don't touch.
GRANT USAGE ON SCHEMA nba TO nba_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA nba TO nba_reader;

-- Ingest: read and write rows, but not change table structure.
GRANT USAGE ON SCHEMA nba TO nba_ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA nba TO nba_ingest;

-- Apply the same grants automatically to tables added by future migrations.
ALTER DEFAULT PRIVILEGES IN SCHEMA nba GRANT SELECT ON TABLES TO nba_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA nba
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nba_ingest;
