# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A portfolio project, focused on the backend. Users ask NBA player-stat questions in plain English. An LLM (Gemini free tier) writes SQL, the SQL is safety-checked and run against Postgres, and the answer comes only from query results. The hosting target is AWS at about $0/month (Lambda + API Gateway + S3/CloudFront, SAM for infrastructure, Neon for Postgres).

Built so far:
- project skeleton and CI
- `nba` schema and database roles
- stats.nba.com downloader, normalizer, loader and backfill CLI. The local DB holds 1946-47 to now: regular season, NBA Cup final, play-in and playoffs, about 1.49M rows and 305 MB. NBA Cup games are tagged with their round.
- season-totals view, and stat-coverage views ("tracked since")
- data-quality checks

Not built yet:
- SQL safety checker
- LLM pipeline and accuracy eval
- API and frontend
- AWS deploy

The phased plan, with decisions, trade-offs and a progress table, is in `docs/PLAN.md`. Update its progress table and any changed decisions in the same branch as the work.

## Commands

Use uv for everything; there's no plain pip workflow. Database tests and the migration runner read `DATABASE_URL`, which `--env-file .env` provides (copy it from `.env.example`).

```bash
uv sync                                                # create .venv, install deps
docker compose up -d                                   # local Postgres 16 on :5432
uv run --env-file .env python -m nba_query_app.db.migrate   # apply migrations

uv run ruff check . && uv run ruff format --check .    # lint + format check (CI runs both)
uv run --env-file .env pytest                          # all tests
uv run --env-file .env pytest tests/test_load.py::test_corrected_stat_is_updated   # one test

uv run --env-file .env python -m nba_query_app.ingest.backfill [--from 2026-27] [--season-type regular|cup-final|play-in|playoffs|all]
uv run --env-file .env python -m nba_query_app.quality      # data-quality report (read-only; the backfill also runs it)
```

The backfill makes real requests to stats.nba.com (about 340 for the full history, two per season and season type, roughly 10 minutes). Run it only when data is actually needed.

Without `DATABASE_URL`, database tests are **skipped**. With `REQUIRE_DATABASE=1` (set in CI) they **fail** instead.

Spike scripts declare their own dependencies inline and run in isolation: `uv run spikes/stats_nba_gamelog.py [season]`.

## Architecture

**Data flow.** `ingest/backfill.py` runs, for each season and season type: fetch the player-level and team-level logs (two requests) → normalize both together → load. Each one commits on its own, so a crash keeps earlier seasons.
- `ingest/fetch.py` calls stats.nba.com `LeagueGameLog`, which returns a whole season in one request: `level="player"` gives one row per player per game, `level="team"` one row per team per game. It retries with increasing waits and raises `FetchError` after the last attempt.
- `ingest/normalize.py` is pure, with no network or database access. It turns the response into a `Batch` of frozen dataclasses: `Team`, `Player`, `Game`, `PlayerGameStats`, and, when the team-level response is passed too, `TeamGameStats` (`normalize_team_game_log`).
- `ingest/load.py` upserts a `Batch` in one transaction.
- `ingest/cup.py` (pure) handles the NBA Cup request, which returns *every* Cup game: `final_only` keeps the final's rows for loading, and `cup_rounds` works out each game's round. A team's first 4 Cup games are group games, its 5th the quarterfinal, its 6th the semifinal, its 7th the final. `load.load_cup_rounds` then tags the stored games; it refuses if a Cup game isn't loaded yet, which is why the regular season comes first in `SEASON_TYPES`.
- Network, sleep and logging are injected as parameters (`request=`, `fetch=`, `sleep=`, `log=`), so tests never hit stats.nba.com or actually wait.

**The dataclasses are the column contract.** `load.py` builds its `INSERT … ON CONFLICT` SQL from `dataclasses.fields()`, so each field name must equal a column name. Adding a column means updating both the migration and the dataclass.
- The one deliberate exception is `games.nba_cup_round`. It's set by a separate UPDATE and kept out of `Game`, so reloading a regular season can't wipe it. Upserts only touch the columns they list.

**Upserts change only what changed.**
- `DO UPDATE … WHERE (cols) IS DISTINCT FROM (EXCLUDED cols)` skips unchanged rows.
- `cur.rowcount` after `executemany` counts rows actually inserted or changed.
- `nba.meta.data_version` is bumped only when something changed. It will be the cache-invalidation key, so reloading identical data must not bump it.
- Team and player names are updated only when `name_as_of` is the same date or newer, so loading an old season can't overwrite a current name.

**Data quirks handled in `normalize.py`** (details in `spikes/FINDINGS.md`):
- **Official, competitive games only** (a deliberate product decision, see `docs/PLAN.md`): exhibitions such as the All-Star game and preseason are excluded.
- `SEASON_ID` such as `'22026'`: the first digit is the season type (2 = regular season, 4 = playoffs, 5 = play-in, 6 = NBA Cup final; anything else, including 1 = preseason and 3 = All-Star, is rejected), then the start year. It becomes `'2026-27'`.
  - The API calls play-in `PlayIn` (from 2019-20); we store it as `Play-In`.
  - The NBA Cup final (from 2023-24) is requested as `IST`, which returns every Cup game. Only the prefix-6 final is loaded as box scores, because the group, quarterfinal and semifinal games count as regular-season games we already have. All Cup games get `nba_cup_round` (see `ingest/cup.py`).
- **The team log decides each game's home/away and W/L.** It has one row per team, and its winner always has the higher score, while a few old player rows carry wrong labels (e.g. 1974-11-19, where the player rows say CLE won with 85 to HOU's 94). Player rows follow it, and overruled labels are counted. The rules below for home/away and W/L are the fallback for games missing from the team log.
- Home/away comes from `MATCHUP`: `'X vs. Y'` means X is home, `'X @ Y'` means X is away.
- Both teams listed as `@` means a **neutral-site game**. Then `home_team_id` and `away_team_id` are NULL, `neutral_site` is true, and `is_home` is NULL for those player rows. A CHECK constraint enforces this.
- **Unrecorded stats are NULL, never 0.** Early seasons lack many stats (no blocks or steals before 1973-74, no 3-pointers before 1979-80, minutes missing in the earliest years). Only `pts`, `fgm` and `ftm` are required.
  - Early seasons report `MIN = 0` for players whose minutes weren't recorded (everyone in the earliest years, some players into the early 1980s). So a 0 becomes NULL when its team's minutes fall 15+ short of the game length (240, plus 25 per overtime, judged from the team with more minutes). A 0 on a team whose minutes add up is real (under 30 seconds).
  - Attempts lower than makes (an impossible value in old box scores) become NULL attempts.
- A W/L missing on one player row is filled in from teammates, or else from the opponent. Every game must end with exactly one winner and one loser (no ties).
- Home/away per team is a majority vote of its rows. A few old rows are mislabelled.
- **A player listed on both teams in one game** (`_resolve_duplicate_player_rows`):
  - Ghost copies are dropped. A copy is "real" if it has points, makes or minutes. Copies with identical scoring collapse to the most complete one.
  - Real copies with different scoring are all kept. That's the 1978-11-08 protested 76ers–Nets game, finished after a trade, so the table key is `(game_id, player_id, team_id)`.
- `MIN` is a whole number. Shooting percentages from the source are ignored, since they can be derived.
- Bad input raises `NormalizeError` rather than loading partial data.
- Every time a cleanup rule above fires, it's counted in `Batch.cleanups` (rule names in `normalize.CLEANUP_RULES`). The backfill logs the counts per season and the totals at the end. A new cleanup rule needs a counter too.
- When data trips a new rule, dry-run fetch + normalize over all seasons (no DB) to find every case at once, then look at the actual rows before writing a rule. Several "obvious" rules here turned out to drop real stats.

**Season totals** are the view `nba.player_season_stats` (one row per player, season and season type, all teams combined), calculated from the game rows so the two always agree. A total is NULL unless the stat was recorded in every one of the player's games that season, so partial coverage never undercounts. It's a plain view, not a stored copy: a full-history scan takes about 0.5 s locally.

**Stat coverage.** `nba.stat_coverage` (share of player rows with each stat, per season) and `nba.stat_tracked_since` (per stat: first season with any value, and the first season from which every season is at least 98% recorded) are computed from the data, never typed in. They drive the refuse-or-caveat rules for early seasons. The threshold is 98%, not 99%, because 1984-85 sits at about 98.5% for most stats (a few players have partial box scores all season).

**Data-quality checks** (`quality.py`) are SQL queries that each return `(key, detail)` rows, one per violation: impossible values, points or rebounds that don't add up, values above all-time records, winners with fewer points, team minutes well short of the game length (usually a player row missing from the source), uneven games per team, and spot-checks against well-known official totals (`KNOWN_TOTALS`, e.g. Kareem's 38,387 career points). They only report; they never block a load or change data. Violations that were investigated and accepted go in `KNOWN_EXCEPTIONS` (a regex on the key, plus the reason), so new problems stand out. Queries get a `%(current_season)s` parameter, so a literal `%` must be written `%%`. The exit code is 1 if anything unexplained is found.

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

- **stats.nba.com blocks cloud IPs**, so ingestion runs from a home machine. cdn.nba.com returned 403 even from home and was dropped. The daily update will be a local scheduled job that re-fetches the current season (two requests: player and team logs), relying on idempotent upserts.
- **Data goes into the local Docker database for now.** Neon is set up and loaded in step 17 of `docs/PLAN.md`.
- **The Gemini free tier has tight per-minute and daily limits.** Tests must never call the real LLM, so use stubs. The accuracy eval runs only when triggered by hand.
- **The LLM doesn't know today's date.** Always pass the current date, the current season and `nba.meta.latest_game_date` into the prompt.
- **GitHub Actions:** `astral-sh/setup-uv` no longer publishes moving major tags, so pin an exact version (e.g. `@v10.3.3`). CI runs only on pushes to `main` and on pull requests.

## Working agreement with the user

- The user reviews every change **commit by commit** in VS Code's Source Control Graph. Work on one branch per feature or plan step, with small focused commits, each passing lint and tests on its own.
- **Keep branches local until the user says "merge".** Don't push or open a PR before that.
  - Before asking for review, run what CI runs, locally: `ruff check`, `ruff format --check`, and the full test suite against Postgres, for each commit on its own.
  - On "merge": push the branch, open a PR (`gh pr create`), wait for CI to pass, then `gh pr merge --merge --delete-branch`, pull `main`, and confirm CI passes on `main`.
  - If CI fails at that point, stop and show the user the fix before merging.
- Explain things in plain language and define infrastructure, AWS and database jargon. The user isn't familiar with many of these terms.
- **Keep docs and comments in sync with the code, in the same commit as the change that affects them.** That covers:
  - `README.md` (commands, layout)
  - `docs/PLAN.md` (progress table, changed decisions)
  - this file (commands, architecture, constraints)
  - `spikes/FINDINGS.md`
  - module and function docstrings
  - inline comments
  - SQL column `COMMENT`s, which are fed to the LLM

  A stale comment is a bug, so fix it when you see one. Before asking for review, check that nothing in the diff contradicts these files.
