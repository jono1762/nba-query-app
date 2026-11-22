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

## Loading NBA data

Data comes from stats.nba.com, which blocks cloud servers, so run this from a home
connection. A whole season is two requests (player and team logs). Reruns are safe:
unchanged rows are skipped.

```bash
uv run --env-file .env python -m nba_query_app.ingest.backfill                 # 1946-47 to now (~10 min)
uv run --env-file .env python -m nba_query_app.ingest.backfill --from 2026-27  # just the current season
uv run --env-file .env python -m nba_query_app.ingest.backfill --help          # all options
uv run --env-file .env python -m nba_query_app.quality                         # data-quality report
```

The backfill ends with the data-quality report. It flags impossible or suspicious data
(e.g. a team whose player minutes don't add up) for a person to look at; it never
blocks a load.

## Database

- `migrations/` holds numbered SQL files, applied in order, once each.
- `nba` schema: `teams`, `players`, `games`, `player_game_stats` (one row per
  player per team per game, from 1946-47), and `meta` (data version and newest game date).
  Stats that weren't recorded in early seasons are NULL ("not recorded"), not 0.
- `nba.team_game_stats`: one row per team per game (final score and team totals), from
  the source's team-level log.
- Playoff games have their round and game number in the series (`nba.games.playoff_round`,
  `playoff_round_name`, `playoff_series_game`), inferred from who played whom. The
  `nba.playoff_series` view has one row per series: winner, loser and score.
- `nba.player_season_stats` view: season totals per player, calculated from the game
  rows. A total is NULL unless the stat was recorded in every one of the player's games.
- `nba.stat_coverage` and `nba.stat_tracked_since` views: how completely each stat was
  recorded per season, and the season each became fully recorded (at least 98% of rows).
  They drive the AI's refuse-or-caveat rules for early seasons.
- Roles (permission groups, no passwords):
  - `nba_reader`: read-only. All LLM-written SQL runs as a member of this role.
  - `nba_ingest`: can load data, but not change table structure.

  Each environment creates its own login users and grants them one of these.

## Project layout

```
src/nba_query_app/   application code (db/: migrations runner, ingest/: NBA data loading,
                     quality.py: data-quality checks)
migrations/          database schema changes (numbered SQL files)
tests/               automated tests
docs/PLAN.md         the build plan, decisions and progress
spikes/              throwaway experiments and their findings (see spikes/FINDINGS.md)
```
