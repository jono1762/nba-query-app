# NBA Query App

Ask questions about NBA player stats in plain English. An LLM turns the question
into SQL, the SQL is safety-checked and run against a Postgres database of real
NBA data, and the answer comes from the query results.

Status: early development.

## Local development

Requirements: [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync                      # create .venv and install dependencies
cp .env.example .env         # local settings (git-ignored)
docker compose up -d         # start local Postgres on localhost:5432
uv run --env-file .env python -m nba_query_app.db.migrate   # create/update tables

uv run ruff check .                  # lint
uv run ruff format .                 # auto-format
uv run --env-file .env pytest        # all tests (database tests need Postgres running)
```

## Database

- `migrations/` holds numbered SQL files, applied in order, once each.
- `nba` schema: `teams`, `players`, `games`, `player_game_stats` (one row per
  player per game), and `meta` (data version and newest game date).
- Roles (permission groups, no passwords):
  - `nba_reader`: read-only. All LLM-written SQL runs as a member of this role.
  - `nba_ingest`: can load data, but not change table structure.

  Each environment creates its own login users and grants them one of these.

## Project layout

```
src/nba_query_app/   application code
migrations/      database schema changes (numbered SQL files)
tests/           automated tests
spikes/          throwaway experiments and their findings (see spikes/FINDINGS.md)
```
