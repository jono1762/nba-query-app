# NBA Query App: Plan

A living document: update it whenever a decision changes. It's written in plain
language on purpose.

## What we're building
A website where someone types a question like *"Who scored the most points in March 2025?"*. Behind the scenes:
1. An AI model (Google's Gemini) turns the question into a database query (SQL).
2. Our code checks that the query is safe, then runs it against a database of NBA stats.
3. The website shows the answer, taken from the actual data and not from the AI's memory.

Decisions:
- **Data:** every player's stats for every game (regular season and playoffs), plus season totals, from 2000-01 to today. Source: stats.nba.com only.
- **AWS setup tool:** AWS SAM.
- **Frontend:** a plain webpage with no framework.
- **Access:** anyone can use it, with a limit on how often each visitor can ask.

---

## Progress

| Phase | What | Status |
|---|---|---|
| 0 | Data-source spikes (stats.nba.com, cdn.nba.com) | Done, see `spikes/FINDINGS.md` |
| 0 | Gemini and Neon spikes | Not started (needs keys) |
| 1a | Project skeleton, lint, tests, Docker Postgres, CI | Done |
| 1b | Tables and database roles | Done |
| 2a | Convert NBA data into table rows; idempotent loader | Done |
| 2b | History download, into the **local database first** | Next |
| 2c | Season-totals view and spot-checks | |
| 3 | SQL safety checker | |
| 4 | Connect Gemini | |
| 5 | Accuracy test | |
| 6 | Web API | |
| 7 | Webpage | |
| 8 | AWS deploy (and copy the data to Neon) | |
| 9 | Daily update | |
| 10 | Polish | |

---

## Glossary
| Term | What it means here |
|---|---|
| **Lambda** | AWS runs our Python code only when a request comes in. We pay nothing when it's idle, and the free allowance covers a hobby project. |
| **API Gateway** | The public web address that forwards requests to our Lambda. It can also cap how many requests come in per second. |
| **S3** | AWS file storage. It holds our webpage files (HTML/JS). |
| **CloudFront** | AWS's content delivery network. It serves the webpage quickly and sends `/api/...` requests to our backend, so everything sits under one web address. |
| **SSM Parameter Store** | A free, secure place in AWS for secrets like the Gemini API key and the database password. They never go into the code or the browser. |
| **SAM** | A config file (YAML) that describes all the AWS pieces, so one command builds them all and the setup is repeatable. This is "infrastructure as code". |
| **Neon** | A free hosted Postgres database. It goes to sleep when unused and wakes in under a second on the next request. |
| **Postgres role** | A database user account with its own permissions. We make one that can only *read*, and AI-written queries run only as that user. |
| **Schema** | A folder inside the database that groups tables. We use `nba` for stats and `app` for our own bookkeeping. |
| **View** | A saved query that acts like a table. Season totals are calculated from game rows, so the two always agree. |
| **sqlglot** | A Python library that reads a SQL query and breaks it into parts, so we can check it properly instead of trusting it. |
| **FastAPI** | The Python web framework for our backend. **Mangum** is a small adapter that lets the same FastAPI code run on Lambda. |
| **Docker / docker-compose** | Runs Postgres on your laptop in a container, so local setup is a single command. |
| **GitHub Actions** | Runs the checks and tests on every push to `main` and every pull request. Later it also deploys to AWS. **OIDC** lets GitHub sign in to AWS without storing an AWS password in GitHub. |
| **Migrations** | Numbered SQL files that create or change the tables, run in order, so every database (laptop, CI, Neon) ends up identical. |
| **Upsert** | "Insert, or update if it already exists." Loading the same data twice therefore never creates duplicates. |
| **Eval / test set** | About 30 questions where we already know the right answer. We run the app on them and get a % correct score. |
| **Spike** | A quick throwaway experiment to answer "does this even work?" before we build on it. |
| **NAT gateway / WAF / Secrets Manager** | AWS products that would cost money every month. The plan avoids all three. |

---

## How the pieces connect

```
Visitor's browser
   │
   ▼
CloudFront ──► S3 (the webpage files)
   │
   └─ /api/ask ──► API Gateway (caps request rate) ──► Lambda (our Python backend)
                                                         ├─► reads secrets from SSM
                                                         ├─► asks Gemini to write SQL
                                                         └─► runs SQL on Neon Postgres

On your laptop:
  one time:   history download ──► stats.nba.com (2000-01 to today) ──► database
  every day:  scheduled update ──► stats.nba.com (current season)    ──► database
```

**Why the downloads run on your laptop:** stats.nba.com blocks cloud servers but allows home internet. We tested cdn.nba.com as a cloud-friendly alternative, but it refused even home requests (403), so we dropped it. Each daily run re-fetches the whole current season in one request and adds only what's new, so missed days catch up automatically.

---

## What happens when someone asks a question
1. **Rate limit check.** Each visitor gets a fixed number of questions per hour (e.g. 10). There's also an overall daily cap so we never use up the free Gemini allowance.
2. **Cache check.** If the same question was already asked since the last data update, we return the saved answer and skip the AI call.
3. **Build the AI instructions.** These include:
   - the table layout, with column descriptions
   - basketball terms ("triple-double")
   - a few example questions with their correct SQL
   - **today's date, the current season ("2026-27"), and the date of the latest game in the database**
4. **Gemini replies in a fixed format:** "can this be answered?", a reason, and the SQL. If the answer is no, we politely refuse.
5. **Safety check on the SQL.**
   - It must be a single read-only query (SELECT).
   - It may only touch the NBA stats tables, not Postgres's built-in system tables.
   - No dangerous functions.
   - At most 100 rows are returned.
6. **Run it** as the read-only database user, with a 5-second time limit. Even if step 5 missed something, the database itself refuses any writes.
7. **If the query errors,** send the error back to Gemini to fix, at most 2 times, then give up cleanly.
8. **Show** the results table, the SQL (behind a "Show SQL" toggle), and optionally a one-sentence summary. The summary is off by default because it needs a second AI call.
9. **Log** each request: question, SQL, success or failure, retries, AI tokens used, estimated cost, and time taken.

---

## Build order (each bullet is roughly one branch)

**Phase 0: Quick experiments**
- Done: stats.nba.com returns a whole season in one request and works from home. cdn.nba.com is blocked (403).
- Still to do: Gemini (which model is free, its limits, whether the fixed reply format is reliable) and Neon (connection, wake-up delay). These need the keys.

**Phase 1: Foundation**
- 1a: Project skeleton, code-style checker, test runner, Docker Postgres, GitHub Actions. Done.
- 1b: Tables, database users, and tests proving the read-only user can't change data. Done.

**Phase 2: Load the data**
- 2a: Convert NBA's data into our tables; loading twice changes nothing. Handles neutral-site games and team renames. Done.
- 2b: History download script for every season from 2000-01, regular season and playoffs. It can resume if interrupted and runs into the **local database for now**. Copying to Neon happens when we deploy (Phase 8).
- 2c: Season-totals view, and spot-checks against well-known real numbers.

**Phase 3: Safety checker (no AI yet)**
- The SQL checker, with many "try to break it" tests: writes, sneaking in two queries, system tables, slow-down tricks, and so on.

**Phase 4: Connect the AI**
- 4a: Build the AI instructions and call Gemini. A command-line version comes first.
- 4b: Retry on errors, refuse when needed, log token usage and cost.

**Phase 5: Accuracy test**
- About 30 questions with known correct answers, plus about 6 the app *should refuse*.
- They run against a frozen copy of a few seasons, so the correct answers never change. The output is a score like "27/30".
- It runs only when triggered by hand, to save the free AI allowance.

**Phase 6: Web API.** The `/api/ask` endpoint with rate limiting, caching, and clear error messages. Its tests use a fake AI.

**Phase 7: Webpage.** A question box, example questions, a results table, a "Show SQL" button, and friendly messages for refusals and limits.

**Phase 8: Put it on AWS**
- 8a: Set up Neon and copy the local data to it. Add the SAM config for the AWS pieces and a **billing alert at $1**. The first deploy is by hand.
- 8b: Automatic deploy whenever code is merged to `main`.

**Phase 9: Daily update.** A scheduled task on your laptop fetches the current season, upserts it, and clears the cache. Missed days catch up automatically.

**Phase 10: Polish.** A README with the diagram, accuracy score, cost per question, and an explanation of the safety measures.

---

## Trade-offs
- **One database for everything.** Rate limits, cache and logs go in the same Postgres as the stats: simpler. Downside: even a cached answer wakes Neon, which takes under a second.
- **Upload the code as a zip, not a Docker image.** Simpler on AWS. Docker is still used locally and in testing.
- **Season totals are calculated from game rows.** They always match the game data; rare edge cases may differ slightly from NBA.com.
- **The one-sentence AI summary is off by default.** It would double AI usage per question.
- **Accuracy is tested on frozen data.** That keeps the score repeatable. Live data is checked in Phase 2c and Phase 9.
- **Downloads run from a home machine,** because of stats.nba.com's block on cloud servers. The website itself is fully in the cloud.

## How we'll know it works
- Every push and pull request: the code-style check and all tests pass in GitHub Actions.
- Safety: the "try to break it" tests pass, *and* the database itself rejects writes from the read-only user.
- Accuracy: the eval score is recorded in the README.
- Full run-through: ask questions in the browser, locally and on the live site. Check that the logs show token counts and cost, that the next morning's data includes last night's games, and that the $1 billing alert exists.
