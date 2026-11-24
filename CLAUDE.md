# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A portfolio project, focused on the backend. Users ask NBA player-stat questions in plain English. An LLM (Gemini free tier) writes SQL, the SQL is safety-checked and run against Postgres, and the answer comes only from query results. The hosting target is AWS at about $0/month (Lambda + API Gateway + S3/CloudFront, SAM for infrastructure, Neon for Postgres).

Built so far:
- project skeleton and CI
- `nba` schema and database roles
- stats.nba.com downloader, normalizer, loader and backfill CLI. The local DB holds 1946-47 to now: regular season, NBA Cup final, play-in and playoffs, about 1.49M player rows, 147K team rows and 286 MB. NBA Cup games are tagged with their round.
- team box scores (final score and team totals) from the team-level log
- playoff rounds and series (`games.playoff_round*` columns, `nba.playoff_series` view)
- player details (`nba.player_details`: position, height, weight, birth date, college, country, draft)
- season-totals view, and stat-coverage views ("tracked since")
- data-quality checks
- SQL safety checker, and a runner that runs checked SQL as the read-only user
- Gemini client, AI instructions, and the two-call flow that answers a question, with a command-line version

Not built yet:
- retries, refusals and cost logging (step 13)
- accuracy eval
- API and frontend
- AWS deploy

The phased plan, with decisions, trade-offs and a progress table, is in `docs/PLAN.md`. Update its progress table and any changed decisions in the same branch as the work.

## Commands

Use uv for everything; there's no plain pip workflow. Database tests and the migration runner read `DATABASE_URL` (the admin login); anything that runs AI-written SQL reads `READER_DATABASE_URL` (a read-only login). `--env-file .env` provides both (copy it from `.env.example`).

```bash
uv sync                                                # create .venv, install deps
docker compose up -d                                   # local Postgres 16 on :5432
uv run --env-file .env python -m nba_query_app.db.migrate   # apply migrations
docker compose exec db psql -U nba -c "CREATE ROLE nba_app_reader LOGIN PASSWORD 'reader_local_dev' IN ROLE nba_reader"   # once: read-only login (READER_DATABASE_URL)

uv run ruff check . && uv run ruff format --check .    # lint + format check (CI runs both)
uv run --env-file .env pytest                          # all tests
uv run --env-file .env pytest tests/test_load.py::test_corrected_stat_is_updated   # one test

uv run --env-file .env python -m nba_query_app.ingest.backfill [--from 2026-27] [--season-type regular|cup-final|play-in|playoffs|all]
uv run --env-file .env python -m nba_query_app.ingest.player_details   # player details only (first run ~2 h for birth dates)
uv run --env-file .env python -m nba_query_app.quality      # data-quality report (read-only; the backfill also runs it)
uv run --env-file .env python -m nba_query_app.ask "question"   # ask the real AI (up to 2 calls from the free allowance)
```

The backfill makes real requests to stats.nba.com (about 340 for the full history, two per season and season type, roughly 10 minutes). Run it only when data is actually needed.

Without `DATABASE_URL`, database tests are **skipped**. With `REQUIRE_DATABASE=1` (set in CI) they **fail** instead.

Spike scripts declare their own dependencies inline and run in isolation: `uv run spikes/stats_nba_gamelog.py [season]`, `uv run --env-file .env spikes/gemini_free_tier.py probe|format basic|hard <model>...`.

## Architecture

**Data flow.** `ingest/backfill.py` runs, for each season and season type: fetch the player-level and team-level logs (two requests) → normalize both together → load. Each one commits on its own, so a crash keeps earlier seasons.
- `ingest/fetch.py` calls stats.nba.com `LeagueGameLog`, which returns a whole season in one request: `level="player"` gives one row per player per game, `level="team"` one row per team per game. It also fetches player details: `fetch_player_index` (every player in history, one request) and `fetch_player_info` (one player, with birth date; `None` when the source has no record, which it signals with an empty body). All requests retry with increasing waits and raise `FetchError` after the last attempt.
- `ingest/normalize.py` is pure, with no network or database access. It turns the response into a `Batch` of frozen dataclasses: `Team`, `Player`, `Game`, `PlayerGameStats` and `TeamGameStats`. It takes the player-level and team-level responses together (`normalize_team_game_log` handles the team one).
- `ingest/load.py` upserts a `Batch` in one transaction.
- `ingest/player_details.py` loads `nba.player_details` (position, height, weight, college, country, draft) from the player index in one request, then looks up birth dates one player at a time for players not yet checked (`birth_date_checked`), saving each as it arrives. So the first run takes about 2 hours, later ones only fetch new players, and an interrupted run resumes. Birth dates are kept out of the `PlayerDetails` dataclass and set by a separate UPDATE, like `nba_cup_round`, so reloading the index can't wipe them. NBA.com's placeholder for an unknown birth date (a day in early April 1905) is stored as NULL and counted. The backfill runs it at the end; it also has its own CLI.
- `ingest/cup.py` (pure) handles the NBA Cup request, which returns *every* Cup game: `final_only` keeps the final's rows for loading, and `cup_rounds` works out each game's round. A team's first 4 Cup games are group games, its 5th the quarterfinal, its 6th the semifinal, its 7th the final. `load.load_cup_rounds` then tags the stored games; it refuses if a Cup game isn't loaded yet, which is why the regular season comes first in `SEASON_TYPES`.
- `ingest/playoffs.py` (pure) infers each playoff game's round and game number within its series from who played whom, since the source's round labels are unreliable before 2001-02. Completed playoffs count back from the Finals (the one series whose winner plays no later series), which handles old byes and uneven brackets. Playoffs in progress count forward with the current 4-round format. Anything that isn't a knockout bracket (a team in two series at once, a loser playing on) returns None: that's the 1953-54 round robin, left without rounds and counted. `normalize.py` adds the result to the playoff `Game` rows, because one playoffs request holds the whole season's playoffs.
- Network, sleep and logging are injected as parameters (`request=`, `fetch=`, `sleep=`, `log=`), so tests never hit stats.nba.com or actually wait.

**The dataclasses are the column contract.** `load.py` builds its `INSERT … ON CONFLICT` SQL from `dataclasses.fields()`, so each field name must equal a column name. Adding a column means updating both the migration and the dataclass.
- The deliberate exceptions are `games.nba_cup_round` and `player_details.birth_date` (plus `birth_date_checked`). They're set by separate UPDATEs and kept out of the dataclasses, so reloading a regular season or the player index can't wipe them. Upserts only touch the columns they list.

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

**Team box scores** (`nba.team_game_stats`, one row per team per game) come straight from the team-level log, not from adding up player rows, so scores and team totals stay right in games where the source lost a player. A CHECK enforces `won = (pts > opponent_pts)`.

**Season totals** are the view `nba.player_season_stats` (one row per player, season and season type, all teams combined), calculated from the game rows so the two always agree. A total is NULL unless the stat was recorded in every one of the player's games that season, so partial coverage never undercounts. It's a plain view, not a stored copy: a full-history scan takes about 0.5 s locally.

**Stat coverage.** `nba.stat_coverage` (share of player rows with each stat, per season) and `nba.stat_tracked_since` (per stat: first season with any value, and the first season from which every season is at least 98% recorded) are computed from the data, never typed in. They drive the refuse-or-caveat rules for early seasons. The threshold is 98%, not 99%, because 1984-85 sits at about 98.5% for most stats (a few players have partial box scores all season).

**Data-quality checks** (`quality.py`) are SQL queries that each return `(key, detail)` rows, one per violation: impossible values, points or rebounds that don't add up, values above all-time records, player points that don't add up to the team's score in the team log, team minutes that aren't a valid game length or (for player rows) fall well short of the game length (usually a player row missing from the source), uneven games per team, playoff games without an inferred round or whose round differs from the game ID (from 2001-02), players without details or a birth date, implausible ages (under 18 or over 45), draft years more than a year after a player's first season, and spot-checks against well-known official totals and champions (`KNOWN_TOTALS`, e.g. Kareem's 38,387 career points; `KNOWN_CHAMPIONS`). They only report; they never block a load or change data. Violations that were investigated and accepted go in `KNOWN_EXCEPTIONS` (a regex on the key, plus the reason), so new problems stand out. Queries get a `%(current_season)s` parameter, so a literal `%` must be written `%%`. The exit code is 1 if anything unexplained is found.

**SQL safety checker** (`query/safety.py`, pure): `check_sql` parses AI-written SQL with sqlglot and raises `UnsafeSqlError` (with a reason fit to send back to the AI) unless it passes allowlists. Anything not listed is refused.
- Exactly one statement, and it must be SELECT (with WITH/UNION/INTERSECT/EXCEPT). Writes hidden inside it (`WITH d AS (DELETE … RETURNING *)`), `SELECT … INTO`, `FOR UPDATE`, `WITH RECURSIVE` and anything sqlglot only half-parses (`exp.Command`) are refused.
- Tables: only `ALLOWED_TABLES` in the `nba` schema, bare or `nba.`-qualified. WITH names may not start with `pg_`, because Postgres searches `pg_catalog` first, so a `pg_` name outside the WITH's scope would reach a system table. **A new table or view must be added to `ALLOWED_TABLES`**; a test compares it with the database.
- Functions: sqlglot gives known functions their own node class (`ALLOWED_FUNCTION_TYPES`; note that AND, OR, CASE branches, `~` and `~*` are function nodes too) and the rest `exp.Anonymous` (`ALLOWED_FUNCTION_NAMES`). Schema-qualified calls and `reg*` casts are refused. sqlglot renames some functions (`to_char` is `TimeToStr`, `string_agg` is `GroupConcat`), so check the node class with `sqlglot.parse_one(sql, read="postgres")` before adding one.
- Brackets nested more than about 45 deep hit Python's recursion limit inside sqlglot; that's refused as "nested too deeply".

**Running AI-written SQL** (`query/run.py`): `run_query(conn, sql)` checks the SQL, then `run_read_only` runs it with Postgres's own guards, which hold even if the checker misses something. Tests call `run_read_only` directly to prove that.
- It refuses to start (`WriteAccessError`) if the user can write to any `nba` table, so connecting as the admin by mistake fails loudly. Locally that's `nba_app_reader` (`READER_DATABASE_URL`, created once with the command above).
- One transaction: `SET TRANSACTION READ ONLY`, then `statement_timeout` (5 s by default) and `search_path = nba` as `SET LOCAL`, which end with the transaction, so the connection can be reused. The connection must not already be in a transaction.
- The SQL as written is run (not sqlglot's regenerated version, which renames functions), through a server-side cursor: `DECLARE … CURSOR FOR` accepts only one SELECT/VALUES with no data-modifying WITH, and only `max_rows + 1` rows (100 by default) leave the server. `QueryResult.truncated` says whether there were more.
- Intervals (e.g. `age(game_date, birth_date)`) come back as Postgres's text, `'18 years 11 mons 25 days'`, not a Python timedelta, which counts only days.
- A timeout raises `QueryTimeoutError`; other database errors (e.g. a misspelt column) come through as psycopg errors.

**Answering a question** (`query/ask.py`). `Answerer.ask(question) -> Answer` is the interface the rest of the app uses; `Answer` holds the status (`answered`, `refused` or `failed`), the text (the answer, the refusal reason, or what went wrong), the SQL, columns, rows, `truncated`, the model, the number of AI calls, summed `Usage` and seconds. `TwoStepAnswerer(llm, conn)` is the base strategy:
1. find the players named (`players.py`) and build the instructions (`prompt.py`) with today's date, the season and the newest game;
2. AI call 1 returns `{answerable, reason, sql}`; not answerable means status `refused`;
3. `run_query` safety-checks and runs the SQL read-only;
4. AI call 2 phrases the answer from the rows only.
- Failures (AI unreachable, a reply in the wrong shape, unsafe SQL, a database error or timeout) come back as status `failed`, not exceptions, so the calls and tokens used are still known. The rows are kept if only the second call failed. Retries are step 13.
- `conn` must be a read-only user's connection with no open transaction; the CLI connects with autocommit. The instructions and the player-name list are read from the database once per `TwoStepAnswerer`.
- Questions are whitespace-collapsed; empty ones or ones over 500 characters fail without an AI call.
- `nba_query_app/ask.py` is the command-line version (`format_answer` prints the result).

**The AI's instructions** (`query/prompt.py`, `query/players.py`).
- Writing SQL: `sql_instructions(describe_schema(conn))` is the system prompt: rules, basketball definitions and three worked examples (written in `prompt.py`), then every `ALLOWED_TABLES` table and column with its COMMENT, read from the database, then `nba.stat_tracked_since` (when each stat was recorded, so early-season questions are refused without a query). `sql_request(question, QuestionContext)` adds today's date, the current season, `meta.latest_game_date` and the players named in the question. The reply follows `SQL_REPLY_SCHEMA`: `{answerable, reason, sql}`.
- Phrasing the answer: `ANSWER_INSTRUCTIONS` plus `answer_request(question, sql, result)`, which gives the model the rows (at most `ANSWER_MAX_ROWS`) and nothing else, and tells it to use only those.
- Player names: `PlayerNames.load(conn).find(question)` matches the question against every player's name, ignoring accents, case, apostrophes and a possessive 's. A full name anywhere counts; a capitalised last name alone counts only if at most 5 players have it and it isn't part of a team name ("Boston Celtics"). Shared names return every player. `with_seasons` adds first and last seasons. The instructions tell the AI to filter on these `player_id`s rather than on names, because every model tested searched `'Luka Doncic'` and missed `'Luka Dončić'`.
- These functions roll back after reading, so the connection has no open transaction when `run_read_only` needs it.

**AI model client** (`llm/`). `client.py` defines what callers need from a model: `LlmClient.generate(system=, prompt=, json_schema=)` returns an `LlmReply` (text, model, `Usage` in input/output/thinking tokens, seconds). Callers take an `LlmClient`, so tests pass a fake and never call the real model. `gemini.py` is the Gemini version over the REST API (no SDK, to keep the Lambda package small).
- The model is a setting: `GEMINI_MODEL`, default `gemini-3.1-flash-lite`, the cheapest model that did the job in testing (see `spikes/FINDINGS.md`). The key comes from `GEMINI_API_KEY`, is sent in a header, never in the URL, and never appears in error messages.
- With `json_schema`, Gemini's structured output forces the reply into that JSON shape. Temperature is 0.
- Errors: `LlmQuotaError` (429, free allowance used up), `LlmUnavailableError` (overloaded, timed out or unreachable; worth retrying), otherwise `LlmError` (including a blocked reply or one cut off before `STOP`). The timeout is 20 s, since API Gateway cuts requests off at 29 s.

**Migrations.** Numbered plain-SQL files in `migrations/`, applied by `db/migrate.py`. Each file runs in its own transaction and is recorded in `public.schema_migrations`. Never edit an applied migration; add a new numbered file. Column `COMMENT`s are deliberate: they're meant to be fed to the LLM as schema documentation, so keep them accurate and plain.

**Roles** (`002_create_roles.sql`) are NOLOGIN groups. Each environment creates its own login users, so no passwords live in migrations.
- `nba_reader`: SELECT only on `nba`. All LLM-generated SQL must run as a member of this role.
- `nba_ingest`: read and write rows, but no DDL or TRUNCATE.

Consequences of this setup:
- `TEMPORARY` is revoked from PUBLIC. No role except the admin can create temp tables, which rules out COPY-into-temp-table loading for `nba_ingest`.
- `ALTER ROLE … SET` (e.g. `statement_timeout`) isn't inherited through group membership. Set it on the login user or per transaction.
- `nba_reader` can still read `pg_catalog`, which every role can by default. The SQL safety checker blocks system catalogs.

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
