# NBA Query App: Plan

A living document: update it whenever a decision changes. It's written in plain
language on purpose.

## What we're building
A website where someone types a question like *"Who scored the most points in March 2025?"*. Behind the scenes:
1. An AI model (Google's Gemini) turns the question into a database query (SQL).
2. Our code checks that the query is safe, then runs it against a database of NBA stats.
3. The website shows the answer, taken from the actual data and not from the AI's memory.

Decisions:
- **Data:** every player's stats for every **official, competitive game**, plus season totals, for the **full history from 1946-47** to today. That means regular season, NBA Cup final (from 2023-24), play-in (from 2019-20) and playoffs. Exhibitions (All-Star, Rising Stars, preseason) are excluded by design. Source: stats.nba.com only. Stats that weren't recorded in early seasons are stored as "not recorded" (NULL), and the AI must refuse or caveat questions about them.
- **AWS setup tool:** AWS SAM.
- **Frontend:** a plain webpage with no framework.
- **Access:** anyone can use it, with a limit on how often each visitor can ask.

---

## Progress

| Step | What | Status |
|---|---|---|
| 1 | Data-source tests (stats.nba.com, cdn.nba.com) | Done, see `spikes/FINDINGS.md` |
| 2 | Project skeleton, lint, tests, Docker Postgres, CI | Done |
| 3 | Tables and database roles | Done |
| 4 | Convert NBA data into table rows; safe reloads | Done |
| 5 | Full history download (1946-47 on, plus play-in and NBA Cup finals), into the **local database first** | Done: 1.49M rows, 305 MB |
| 6 | NBA Cup round on each game (group / quarterfinal / semifinal / final) | Done |
| 7 | Season totals and data-quality checks | In progress: season-totals and stat-coverage views done |
| 8 | SQL safety checker | |
| 9 | Connect Gemini (command-line first) | |
| 10 | Retries, refusals, cost logging | |
| 11 | Accuracy test | |
| 12 | Web API, plus `docs/ARCHITECTURE.md` | |
| 13 | Webpage | |
| 14 | Neon setup and first AWS deploy | |
| 15 | Automatic deploys | |
| 16 | Daily update | |
| 17 | Polish | |

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
  one time:   history download ──► stats.nba.com (1946-47 to today) ──► database
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
6. **Run it** as the read-only database user, with a 5-second time limit. Even if the safety check missed something, the database itself refuses any writes.
7. **If the query errors,** send the error back to Gemini to fix, at most 2 times, then give up cleanly.
8. **Show** the results table, the SQL (behind a "Show SQL" toggle), and optionally a one-sentence summary. The summary is off by default because it needs a second AI call.
9. **Log** each request: question, SQL, success or failure, retries, AI tokens used, estimated cost, and time taken.

---

## Build order (each step is roughly one branch)

**1. Data-source tests.** Done: stats.nba.com returns a whole season in one request and works from home. cdn.nba.com is blocked (403).

**2. Project skeleton.** Code-style checker, test runner, Docker Postgres, GitHub Actions. Done.

**3. Tables and database roles,** with tests proving the read-only user can't change data. Done.

**4. Convert NBA data into rows.** Loading twice changes nothing. Handles neutral-site games and team renames. Done.

**5. Full history download.** Every season from 1946-47 (regular season, NBA Cup final, play-in and playoffs), into the **local database for now**. If it's interrupted, just rerun it. Done: 81 seasons in about 5 minutes, with old-data errors cleaned up (see `spikes/FINDINGS.md`). Copying to Neon happens in step 14.

**6. NBA Cup rounds.** Each Cup game is tagged as group, quarterfinal, semifinal or final. Done.

**7. Season totals and data quality.**
- Season-totals view, and spot-checks against well-known real numbers.
- **"Tracked since" table**, calculated from the data rather than typed-in dates (e.g. "first season where ≥ 99% of rows have blocks"). It drives the AI's refuse-or-caveat rules. Coverage ramps up gradually and unevenly, so these are *not* hard database rules.
- **Data-quality checks** (a script that runs after loading, plus tests). They report violations for a person to look at; they don't block the load. They flag:
  - *Impossible values:* 3-pointers before 1979-80 (no 3-point line yet), play-in before 2019-20.
  - *Stats that disappear* after being fully recorded.
  - *Implausible numbers:* a player's points ≠ 2 × (FGM − FG3M) + 3 × FG3M + FTM where all are recorded (FGM includes 3-pointers, so 2-pointers are FGM − FG3M), extreme single-game values.
  - *Team totals that don't add up* within a game, and any game without exactly one winner and one loser (also enforced when loading).
  - *Games per team per season*, the closest we can get to proving no games are missing:
    - Count each team's regular-season games and compare with that season's schedule length (82 in modern seasons; it changed over history). In NBA Cup seasons every team should still have exactly 82, with the two finalists also having one "NBA Cup Final" game.
    - Report seasons where teams' counts differ. Some are real: early seasons with uneven schedules, the 2012-13 Celtics–Pacers game cancelled after the Boston Marathon bombing, and the COVID-shortened 2019-20. A small list of known exceptions keeps the report quiet for those, so anything else stands out.
- **Count every time a cleanup rule fires** during loading (attempts made "not recorded", W/L filled in, home/away majority votes, ghost rows dropped), so the totals in `spikes/FINDINGS.md` are exact rather than only the cases that first caused errors.
- If a check finds something, look at the actual rows and decide, as with the old box-score errors in step 5.

**8. SQL safety checker (no AI yet).** Many "try to break it" tests: writes, sneaking in two queries, system tables, slow-down tricks, and so on.

**9. Connect Gemini.** First test which model is free, its limits, and whether the fixed reply format is reliable (needs the API key). Then build the AI instructions and call Gemini, with a command-line version first.

**10. Retries, refusals, cost logging.** Retry on errors, refuse when needed, log token usage and cost. Refuse questions about stats before they were recorded ("blocks weren't tracked in 1962"), and add a caveat to totals that span the gap ("blocks counted from 1973-74").

**11. Accuracy test.**
- About 30 questions with known correct answers, plus about 6 the app *should refuse* (including stats asked about before they were recorded).
- They run against a frozen copy of a few seasons, so the correct answers never change. The output is a score like "27/30".
- It runs only when triggered by hand, to save the free AI allowance.

**12. Web API.** The `/api/ask` endpoint with rate limiting, caching, and clear error messages. Its tests use a fake AI.
- Also write `docs/ARCHITECTURE.md` now that a question can travel the whole path. It covers the request flow, the safety layers, the data source, and the key decisions and why. Then trim `CLAUDE.md`'s Architecture section to a pointer plus code-level details. (Deferred until here so it describes the real system, not the plan.)

**13. Webpage.** A question box, example questions, a results table, a "Show SQL" button, and friendly messages for refusals and limits.

**14. Neon setup and first AWS deploy.** Test the Neon connection and its wake-up delay, check the real free storage limit, and copy the local data to it. Add the SAM config for the AWS pieces and a **billing alert at $1**. The first deploy is by hand.

**15. Automatic deploys** whenever code is merged to `main`.

**16. Daily update.** A scheduled task on your laptop fetches the current season, upserts it, and clears the cache. Missed days catch up automatically.

**17. Polish.** A README with the diagram, accuracy score, cost per question, and an explanation of the safety measures.

---

## Which games count
**Official, competitive games only:** regular season, NBA Cup final, play-in and playoffs. Exhibitions are excluded, including the All-Star game, Rising Stars and preseason. They don't count in official stats, so mixing them in would inflate totals and every question would have to filter them out. The All-Star game was also checked: the source has it from 1950-51 to 2023-24 but can't reliably identify the new-format games from 2024-25 on.

## Trade-offs
- **One database for everything.** Rate limits, cache and logs go in the same Postgres as the stats: simpler. Downside: even a cached answer wakes Neon, which takes under a second.
- **Upload the code as a zip, not a Docker image.** Simpler on AWS. Docker is still used locally and in testing.
- **Season totals are calculated from game rows.** They always match the game data; rare edge cases may differ slightly from NBA.com.
- **The one-sentence AI summary is off by default.** It would double AI usage per question.
- **Accuracy is tested on frozen data.** That keeps the score repeatable. Live data is checked in steps 7 and 16.
- **Full history, with gaps.** Going back to 1946-47 means many stats are "not recorded" for older games. The cost is extra AI rules and tests (steps 7, 10, 11) and storage: 305 MB, about 60% of Neon's free 0.5 GB. Check the real limit in step 14.
- **Downloads run from a home machine,** because of stats.nba.com's block on cloud servers. The website itself is fully in the cloud.

## How we'll know it works
- Every push and pull request: the code-style check and all tests pass in GitHub Actions.
- Safety: the "try to break it" tests pass, *and* the database itself rejects writes from the read-only user.
- Accuracy: the eval score is recorded in the README.
- Full run-through: ask questions in the browser, locally and on the live site. Check that the logs show token counts and cost, that the next morning's data includes last night's games, and that the $1 billing alert exists.
