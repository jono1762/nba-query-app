# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A portfolio project, focused on the backend. Users ask NBA player-stat questions in plain English. An LLM (Gemini free tier) writes SQL, the SQL is safety-checked and run against Postgres, and the answer comes only from query results. The hosting target is AWS at about $0/month (Lambda + API Gateway + S3/CloudFront, SAM for infrastructure, Neon for Postgres).

Built so far:
- project skeleton and CI
- `nba` schema and database roles
- stats.nba.com normalizer and loader

Not built yet:
- history download CLI
- SQL safety checker
- LLM pipeline and accuracy eval
- API and frontend
- AWS deploy

The phased plan lives outside the repo at `~/.claude/plans/pasted-content-id-0ccc-project-nba-dynamic-flask.md`.

## Commands

Use uv for everything; there's no plain pip workflow. Database tests and the migration runner read `DATABASE_URL`, which `--env-file .env` provides (copy it from `.env.example`).

```bash
uv sync                                                # create .venv, install deps
docker compose up -d                                   # local Postgres 16 on :5432
uv run --env-file .env python -m nba_query_app.db.migrate   # apply migrations

uv run ruff check . && uv run ruff format --check .    # lint + format check (CI runs both)
uv run --env-file .env pytest                          # all tests
uv run --env-file .env pytest tests/test_load.py::test_corrected_stat_is_updated   # one test
```

Without `DATABASE_URL`, database tests are **skipped**. With `REQUIRE_DATABASE=1` (set in CI) they **fail** instead.

Spike scripts declare their own dependencies inline and run in isolation: `uv run spikes/stats_nba_gamelog.py [season]`.

## Architecture

**Data flow.** stats.nba.com `LeagueGameLog` (player mode) returns a whole season in one request.
- `ingest/normalize.py` is pure, with no network or database access. It turns the response into a `Batch` of frozen dataclasses: `Team`, `Player`, `Game`, `PlayerGameStats`.
- `ingest/load.py` upserts a `Batch` in one transaction.

**The dataclasses are the column contract.** `load.py` builds its `INSERT … ON CONFLICT` SQL from `dataclasses.fields()`, so each field name must equal a column name. Adding a column means updating both the migration and the dataclass.

**Upserts change only what changed.**
- `DO UPDATE … WHERE (cols) IS DISTINCT FROM (EXCLUDED cols)` skips unchanged rows.
- `cur.rowcount` after `executemany` counts rows actually inserted or changed.
- `nba.meta.data_version` is bumped only when something changed. It will be the cache-invalidation key, so reloading identical data must not bump it.
- Team and player names are updated only when `name_as_of` is the same date or newer, so loading an old season can't overwrite a current name.

**Data quirks handled in `normalize.py`** (details in `spikes/FINDINGS.md`):
- `SEASON_ID` such as `'22026'`: the first digit is the season type (2 = regular season, 4 = playoffs; anything else is rejected), then the start year. It becomes `'2026-27'`.
- Home/away comes from `MATCHUP`: `'X vs. Y'` means X is home, `'X @ Y'` means X is away.
- Both teams listed as `@` means a **neutral-site game**. Then `home_team_id` and `away_team_id` are NULL, `neutral_site` is true, and `is_home` is NULL for those player rows. A CHECK constraint enforces this.
- `MIN` is a whole number. Shooting percentages from the source are ignored, since they can be derived.
- Bad input raises `NormalizeError` rather than loading partial data.

**Migrations.** Numbered plain-SQL files in `migrations/`, applied by `db/migrate.py`. Each file runs in its own transaction and is recorded in `public.schema_migrations`. Never edit an applied migration; add a new numbered file. Column `COMMENT`s are deliberate: they're meant to be fed to the LLM as schema documentation, so keep them accurate and plain.

**Roles** (`002_create_roles.sql`) are NOLOGIN groups. Each environment creates its own login users, so no passwords live in migrations.
- `nba_reader`: SELECT only on `nba`. All LLM-generated SQL must run as a member of this role.
- `nba_ingest`: read and write rows, but no DDL or TRUNCATE.

Consequences of this setup:
- `TEMPORARY` is revoked from PUBLIC. No role except the admin can create temp tables, which rules out COPY-into-temp-table loading for `nba_ingest`.
- `ALTER ROLE … SET` (e.g. `statement_timeout`) isn't inherited through group membership. Set it on the login user or per transaction.
- `nba_reader` can still read `pg_catalog`, which every role can by default. The SQL safety checker must block system catalogs.

**Tests.**
- The `test_db_url` and `db` fixtures in `tests/conftest.py` create a fresh, uniquely named database per test and drop it afterwards.
- `connect_as("nba_reader" | "nba_ingest")` creates a temporary login user in that role, to test real permissions.
- Normalizer and loader tests use `tests/fixtures/leaguegamelog_2026-27_two_games.json` (two real games).
- The season date logic is `seasons.season_for_date`. The season flips in October, and July-September counts as the season just ended.

## Constraints to keep in mind

- **stats.nba.com blocks cloud IPs**, so ingestion runs from a home machine. cdn.nba.com returned 403 even from home and was dropped. The daily update will be a local scheduled job that re-fetches the current season (one request), relying on idempotent upserts.
- **The Gemini free tier has tight per-minute and daily limits.** Tests must never call the real LLM, so use stubs. The accuracy eval runs only when triggered by hand.
- **The LLM doesn't know today's date.** Always pass the current date, the current season and `nba.meta.latest_game_date` into the prompt.
- **GitHub Actions:** `astral-sh/setup-uv` no longer publishes moving major tags, so pin an exact version (e.g. `@v10.3.3`). CI runs only on pushes to `main` and on pull requests.

## Working agreement with the user

- The user reviews every change **commit by commit** in VS Code's Source Control Graph. Work on one branch per feature or phase, with small focused commits, each passing lint and tests on its own.
- Push the branch and open a PR (`gh pr create`) so CI runs **before** asking for review. After approval, merge with `gh pr merge --merge --delete-branch`, pull `main`, and confirm CI passes on `main`.
- Explain things in plain language and define infrastructure, AWS and database jargon. The user isn't familiar with many of these terms.
