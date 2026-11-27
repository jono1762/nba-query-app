# Spike findings

Experiments and data investigations. The first sections are step 1 of `docs/PLAN.md`.

Run on 2026-11-18 from a home connection (WSL2 on Windows).

## stats.nba.com (history source): works
- `LeagueGameLog(player_or_team_abbreviation="P")` returns **a whole season in one
  request**, in under a second, with no special headers needed.
  - 2000-01: 23,973 rows, 1,189 games (4.6 MB of JSON)
  - 2026-27 so far: 4,721 rows, 210 games, latest game 2026-11-17 (yesterday)
- So a download needs only one request per season and season type.
- Data is up to date the morning after games, so this source could also handle
  daily updates, as long as it runs from a home connection.
- 32 columns: IDs and names for player, team and game, date, matchup, W/L, and the
  standard box score (MIN, FGM/FGA, FG3M/FG3A, FTM/FTA, OREB/DREB/REB, AST, STL,
  BLK, TOV, PF, PTS, PLUS_MINUS).
- Gotchas:
  - `MIN` is a **whole number** (rounded), not minutes and seconds.
  - `SEASON_ID` is like `'22026'`. The leading `2` means regular season.
  - Game IDs are strings with leading zeros (`'0022600003'`). Store them as text.
  - Opponent and home/away have to be parsed out of `MATCHUP` (`'OKC @ SAS'` is
    away, `'OKC vs. SAS'` is home).
  - Only regular season was fetched. Playoffs need a second call per season
    (`season_type_all_star="Playoffs"`).

## cdn.nba.com (planned daily-update source): blocked
- Every endpoint tried returned **HTTP 403 Forbidden** from the home connection:
  the season schedule and today's scoreboard, with and without browser-like
  headers (User-Agent, Referer, Origin). There was no proxy in the way.
- Not yet tested from AWS or GitHub Actions. It might behave differently there,
  but there's no reason to expect it to work better.

## What this means for the plan
- The history download (step 5) works as planned.
- **Decision (2026-11-18): use stats.nba.com only.** It handles both the history
  download and the daily update. The daily update runs as a scheduled task on a
  home machine instead of in AWS. Each run fetches the whole current season in
  one request and adds only new rows, so missed days catch up on their own.
- cdn.nba.com is dropped. Its test script was deleted, but it's still in git
  history (commit a370626) if a cloud-based update is ever tested from AWS.

## Full history from 1946-47, plus play-in (2026-11-20)
Decision: load every season from 1946-47 (the BAA's first season) and add play-in
games.

- **Load:** 81 seasons × regular season, play-in (from 2019-20) and playoffs. That's
  170 requests in about 5 minutes from an empty database.
- **Result:** 1,487,560 player-game rows, 73,392 games (37 play-in), 5,159
  players, 45 franchises. The database is **286 MB** including the team box scores added later (measured after `VACUUM FULL`, which compacts away space left by reloads), about 56% of Neon's free 0.5 GB.
- **Sanity checks passed:**
  - 1,230 regular-season games in a normal 30-team season (1,189 in 2000-01
    with 29 teams).
  - Shortened seasons show fewer games (2011-12: 990, 2019-20: 1,059).
  - Known records are present: Wilt Chamberlain 100 on 1962-03-02, Kobe 81 on
    2006-01-22, Dončić 73 on 2024-01-26.
  - Reloading a season changes nothing.
- **Missing stats are NULL in the source**, never 0, with one exception: early
  seasons report `MIN = 0` when minutes weren't recorded. That's everyone in the
  earliest years, and some players into the early 1980s (e.g. 20 points in "0
  minutes"). A 0 is stored as NULL when its team's minutes fall 15+ short of the game
  length (240, plus 25 per overtime). That turned about 20,500 more rows from 0 into
  "not recorded", almost all before 1985, and only 5 after 1996. How complete each
  stat is, by season (share of rows with a value):

  | Season | Minutes | Rebounds | Assists | Steals | Blocks | Turnovers | 3PA |
  |---|---|---|---|---|---|---|---|
  | 1946-47 | 0% | 0% | 4% | 0% | 0% | 0% | 0% |
  | 1960-61 | 30% | 71% | 39% | 0% | 0% | 0% | 0% |
  | 1973-74 | 85% | 93% | 92% | 28% | 32% | 0% | 0% |
  | 1979-80 | 99% | 99% | 98% | 71% | 82% | 66% | 86% |
  | 1985-86 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |

  Coverage rises gradually and is often partial within a season. The exact
  seasons are computed from the data by the `nba.stat_tracked_since` view (see
  "Stat coverage" below).

- **Errors in old box scores, and how they're handled** (each found in the real
  data and covered by a test):
  - *Attempts lower than makes* (a 1946-47 player with 1 FT made, 0 attempted):
    the attempts become NULL.
    - Checked whether the column might mean *misses* instead. It doesn't:
      - These rows are rare: 115 values in all, at most 19 of about 6,000 rows per
        season, none after 1966-67.
      - The source's own percentage treats the value as attempts (Bob Feerick
        1947-48: 4 FT made, 1 attempted, shown as 400%).
      - League free-throw percentage only matches history under the attempts
        reading: 1951-52 is 73.5% as attempts vs 42.4% as misses, and the
        historical figure is about 73%.
    - So they're recording errors. Some may be misses typed into the wrong
      column, but nothing proves which, so no value is guessed.
  - *W/L missing on a single player row* (25 rows, all in 1959-60 and 1960-61):
    filled in from teammates.
  - *Wrong home/away or W/L labels on player rows:* the team-level log decides
    (see "Team log" below). For games missing from it, each team's home/away is a
    majority vote of its rows. That vote once picked the wrong side: in 1967-68
    SFW–SDR, 10 rows said SDR was home, but the team log says SFW.
  - *A player listed on both teams in one game:* 11 ghost rows dropped across
    history. Examples:
    - Bob Cousy 1951-52: 13 pts for BOS, plus an empty BAL copy with a stray FT
      attempt.
    - John Shumate 1977-78: the full box score for DET is kept, the partial BUF
      copy dropped.
  - *Genuinely playing for both teams:* the **protested 76ers-Nets game of
    1978-11-08** was finished months later, after a trade, so Eric Money, Ralph
    Simpson and Harvey Catchings have real stats for both teams. All their rows
    are kept, and the table key includes the team for this reason.
    - An earlier rule ("a 0-point copy beside a scoring copy is a ghost") would
      have dropped Simpson's and Catchings' real rows. The fix was to count a copy
      as real if it has points, makes or **minutes**.
- **Known, deliberately kept source anomaly:** Greg Jackson, 1975-03-19 NYK at
  PHX. The source shows 8 pts for NYK (no minutes) and 4 minutes for PHX (no
  points). He'd been traded to PHX earlier that season, so the 8 points probably
  belong to his PHX row, but nothing in the data proves it. Both rows are kept as
  the source has them.
- **Neutral-site games:** 11, all from 2024-25 on, matching known neutral
  venues: Mexico City (MIA-WAS 2024-11-02, DAL-DET 2025-11-01), the Las Vegas NBA
  Cup semifinals (2024-12-14) and Paris (IND-SAS, January 2025). Earlier
  neutral-venue games, common in the 1950s-60s, are listed with a normal home team
  in the source, so they aren't flagged.

## Which games are included (2026-11-20)
Decision: **official, competitive games only.**
- **Included:**
  - regular season
  - NBA Cup final (from 2023-24)
  - play-in (from 2019-20)
  - playoffs
- **Excluded:** exhibitions (All-Star, Rising Stars, preseason).

- **NBA Cup:** the source's "IST" season type returns all 67 Cup games per season.
  - 66 are regular-season games, which the regular-season download already has
    (2024-25 has exactly 30 × 82 ÷ 2 = 1,230 regular-season games).
  - The championship game has season-ID prefix 6 and doesn't count in
    regular-season stats, so it was missing. The 3 finals are now loaded as
    `NBA Cup Final`:
    - 2023 LAL over IND (the source lists it as a LAL home game)
    - 2024 MIL over OKC (neutral site)
    - 2025 NYK over SAS (neutral site)
- **All-Star game,** checked and excluded:
  - The source has it from 1950-51 to 2023-24, under made-up "East/West All
    Stars" teams.
  - For the new format (2024-25 on), the `003…` game IDs mix the All-Star games
    with Rising Stars games, and some return nothing, so the real All-Star games
    can't be identified reliably.

## NBA Cup rounds (2026-11-21)
- **Why the final doesn't count but the other knockout games do:** the league
  schedules only 80 games per team in advance (the 4 group games are among them)
  and leaves 2 December dates open.
  - Teams that miss the knockouts get 2 extra ordinary games.
  - Quarterfinal losers get the quarterfinal plus 1 extra game.
  - Semifinalists play the quarterfinal and semifinal as games 81 and 82.
  - For the two finalists the final would be game 83, so it's left out of
    regular-season stats.
- **Structure in the data** (team-level "IST" logs): every finished Cup had 22
  teams with 4 Cup games, 4 with 5, 2 with 6 and 2 with 7. So a team's 5th, 6th and
  7th Cup games are its quarterfinal, semifinal and final. This rule also works
  while a tournament is in progress: as of 2026-11-21, the 2026-27 Cup has 37
  group games.
- **Result:** each finished Cup is tagged as 60 group games, 4 quarterfinals, 2
  semifinals and 1 final. The brackets chain correctly (2025-26: quarterfinal
  winners ORL, NYK, OKC, SAS; semifinal winners NYK, SAS; NYK won the final).
- Tagging needs the games loaded first. During the first run, 9 current-season
  Cup games played after the last load were refused until the regular season was
  refreshed. The check worked as intended.

## Stat coverage (2026-11-21)
The `nba.stat_tracked_since` view, on the full history. "Fully recorded" means
every season from then on has the stat in at least 98% of player rows.

| Stat | First recorded | Fully recorded from |
|---|---|---|
| Points, FG made, FT made | 1946-47 | 1946-47 |
| FT attempted | 1946-47 | 1948-49 |
| Assists | 1946-47 | 1975-76 |
| Rebounds | 1950-51 | 1975-76 |
| FG attempted | 1946-47 | 1976-77 |
| Minutes | 1950-51 | 1976-77 |
| 3-pointers made | 1979-80 | 1979-80 |
| Personal fouls | 1946-47 | 1980-81 |
| Off./def. rebounds, steals, blocks, turnovers, 3-pointers attempted | 1962-63 to 1979-80 | 1983-84 |
| Plus-minus | 1996-97 | 1996-97 |

- **Why 98% and not 99%:** 1984-85 sits at about 98.5% for most stats. A few
  players have partial box scores all season (e.g. Eddie Johnson, Kansas City Kings:
  73 games with points but no rebounds or minutes). At 99%, every stat would
  start in 1985-86.
- Steals, turnovers and offensive/defensive rebounds each have one to three stray
  values years before the league started counting them (first recorded 1962-63 to
  1970-71, against official starts of 1973-74 and 1977-78). That's why their "first
  recorded" season is so early.

## Data-quality checks (2026-11-21)
The first full run of `nba_query_app.quality` over the whole history, after the
reload with the minutes rule above. Every violation was looked at. All are now
listed in `KNOWN_EXCEPTIONS` with the reason, so the report shows 0 unexplained.

- **Cleanup rules, exact totals for the full history** (with the team log):
  - 11 ghost rows dropped
  - 102 home/away labels overruled, 146 player W/L values corrected and 25 missing
    W/L values filled in (see "Team log" below)
  - 127,544 zero-minute rows made "not recorded"
  - 232 impossible attempt values made "not recorded" (115 in player rows, 117 in
    team rows)
  - 1 unplayed game dropped (the cancelled 2012-13 Celtics–Pacers game)
- **Spot-checks against official totals:** 8 of 11 match exactly, e.g. Kareem's
  38,387 and Jordan's 32,292 career points, Stockton's 15,806 assists and 3,265
  steals, Wilt's 4,029 points in 1961-62, Curry's 402 threes in 2015-16, Eaton's 456
  blocks in 1984-85. Three fall slightly short in the source's game logs: Wilt's
  career points (31,417 vs 31,419), Hakeem's career blocks (3,825 vs 3,830) and
  Wilt's 1960-61 rebounds (2,073 vs 2,149). Kept as they are.
- **Clean:** no 3-pointers before 1979-80, no play-in or Cup games before they
  existed, no single-game value above an all-time record (every record holder
  matches: Wilt's 100 points and 55 rebounds, Skiles' 30 assists, and so on), and
  every NBA Cup finalist won its semifinal.
- **Players the source lost.** stats.nba.com's season log is missing some players
  entirely. In the game's box score their line is there (minutes, points…) but with
  no player ID or name, so the season log drops it:
  - Loy Vaught, Clippers, every game from 1990-91 to 1995-96 (about 395 games; he's
    present from 1996-97). His own game log for those seasons is empty too.
  - One Nets player in 1995-96 (about 55 games).
  - A few other games from 1985-86 to 1996-97, probably the same cause (not
    checked one by one).
  - Found by team minutes: those teams' players add up to about 190-220 of 240
    minutes. **Decision (2026-11-21): document now, repair later.** A repair would
    fetch those games' box scores and attribute a nameless line only where it can be
    proven (e.g. NBA.com's season totals for the player match).
- **Wrong minutes in 2005-06 and 2006-07** (about 70 team-games): the season log
  shows e.g. Michael Redd with 20 minutes on 0020600111, where the box score says
  40:00. **Decision: document now; fix together with the repair above**, since both
  need box scores.
- **Player points that don't match the team score:** 1,181 team-games where the
  player rows don't add up to the team's score in the team log. All but 3 are
  before 1996-97, including the lost-player games of 1990-96. In old games the
  player rows are off (e.g. 0028000198: Atlanta scored 88, but its player rows add
  up to 108). Nothing shows which row is wrong, so the source values are kept. The
  3 later ones:
  - 0029600070 (1996-97 CLE–DEN): Šarūnas Marčiulionis, a Denver player that
    season, is listed under Cleveland. Moving his 7 points and 29 minutes would
    fix both teams' points and minutes exactly, but the source is kept.
  - 0029800661 (1998-99): the Nets' player rows add up to 97, their score is 93.
    Cause unknown.
- **Points that don't add up** (points ≠ 2 × 2-pointers + 3 × 3-pointers + free
  throws): 71 rows, all from 1946-47 to 1963-64. Kept as in the source.
- **3-pointers above field goals:** 4 rows in 1985-86 and 1987-88. Kept.
- **Personal fouls** reached 98% in 1951-52 but then swung between 79% and 99%
  until 1980-81, so they count as fully recorded only from then.
- **Games per team:** every team played the same number of regular-season games
  each season except:
  - tiebreaker games counted as regular season: 1946-47 (Chicago–St. Louis on
    1947-03-31, after the last scheduled day) and 1956-57 (St. Louis against Fort
    Wayne and Minneapolis);
  - uneven early schedules: 1949-50 (17 teams after the NBL merger), 1950-51 (the
    Washington Capitols folded after 35 games), 1952-53 and 1953-54 (69 to 72 games);
  - 2012-13: Boston–Indiana cancelled after the Boston Marathon bombing;
  - 2019-20: COVID-19.
  - Seasons with fewer than 82 games since 1967-68: the lockouts (1998-99: 50,
    2011-12: 66) and COVID-19 (2019-20, 2020-21: 72).

## Team log (2026-11-21)
The team-level `LeagueGameLog` (one row per team per game) is now loaded into
`nba.team_game_stats` with every backfill, one extra request per season and season
type.
- **Coverage:** scores for every game back to 1946-47 (the first game: Toronto 66,
  New York 68). Every stored game has exactly two team rows. Team box-score stats
  are NULL where not recorded, as for players; very old games sometimes lack even
  team field goals or free throws made, so only the score is required.
- **One extra game:** the 2012-13 Celtics–Pacers game, cancelled after the Boston
  Marathon bombing, is listed with no result and 0-0. It's dropped.
- **The team log is self-consistent:** in every game, the team marked W has the
  higher score.
- **The player log isn't always:** 7 games from 1967-68 to 1978-79 disagree with the
  team log on home/away or the result. In 3 the player rows marked the
  lower-scoring team as the winner (e.g. 1974-11-19 CLE @ HOU, 85-94). In 2,
  Atlanta–New Orleans on 1979-01-20 and 21, the player rows' labels look swapped
  between the back-to-back games. **Decision (2026-11-21): the team log decides
  each game's home/away and result**; player rows follow it.
- **Dates:** 3 games in 1978-79 have dates one or two days apart between the logs
  (e.g. 0027800354: 1978-12-23 in the player log, 12-25 in the team log). Dates
  come from the player log; nothing shows which is right.
- **Team minutes:** 240 for regulation plus 25 per overtime, 0 (not recorded) in
  early seasons. Five values are impossible: 239, 247, 249 and 239 in 1960-62, and
  Denver's 168 on 2026-11-14, whose player rows add up to 242. They're stored as
  the source has them and listed by a quality check.

## Playoff rounds (2026-11-22)
- **The source's round labels:** the `CommonPlayoffSeries` endpoint's series ID is
  just the first 9 characters of the game ID (e.g. `0041500401`: 2015-16 playoffs,
  round 4, series 0, game 1), so it adds nothing beyond the game ID. From 2001-02
  every playoff game ID carries the round, and it matches the inferred round in
  every game. Before that, IDs are sequential (round digit 0) or inconsistent. For
  example, the 1975 Finals (Golden State's 4-0 sweep) is split across two series
  IDs, one with round "9", and the 1953-54 Finals has a game under round "0".
- **Decision (2026-11-22): infer rounds from who played whom** (`ingest/playoffs.py`),
  counting back from the Finals. That handles byes (1946-48, 1974-76: top teams
  skipped the first round) and the uneven 1949-50 bracket, where counting forward
  gets the round wrong.
- **Result:** every season from 1946-47 resolves to a knockout bracket with a single
  Finals, except **1953-54**, whose playoffs began with a round robin (three teams
  in each division each playing the other two; the West's second Minneapolis-Rochester
  game wasn't played), then division finals and the Finals. Two pairs met in both
  the round robin and the division finals, so the stages can only be split by date:
  the round robin ended on 1954-03-22 (the East's last game; the West's was 03-21).
  **Decision (2026-11-23): that date is listed by hand** (`ROUND_ROBIN_LAST_DAY`);
  the 11 round-robin games are round 1, `Round Robin`, and the 12 after it are
  inferred as usual (Division Finals, then Minneapolis beating Syracuse 4-3 in the
  Finals). Leaving the season without rounds had dropped that title, giving the
  Lakers 16 championships instead of 17. Every inferred champion matches the real
  list; 11 are spot-checked automatically (`KNOWN_CHAMPIONS`, 1953-54 included).
- **Playoffs in progress** count forward with the current 4-round format. Replaying
  the 1983-84, 2024-25 and 2025-26 playoffs day by day gives every game the same
  round on each day as in the final result.

## Player details (2026-11-22)
- **Two endpoints.** `PlayerIndex` (with `Historical=1`) returns every player in
  history in one request: position, height, weight, college, country and draft.
  It has no birth date; that needs `CommonPlayerInfo`, one request per player.
  The first run looked up 5,159 players in 1 h 54 min (a 1-second pause between
  requests); later runs only look up new players.
- **Coverage** (5,159 players): position 5,111, height 5,112, weight 5,105,
  college 5,155, country 5,158 (82 countries), birth date 5,143, draft year 3,722.
  141 players have a draft year but no round (early territorial and other special
  picks, e.g. Wilt Chamberlain in 1959). The 48 players without a position are
  almost all from before 1960.
- **Blanks:** the index writes "not listed" as an empty string, a space or `-`;
  all become NULL.
- **Missing players:** Bobby Watson (Minneapolis, 1952-53) isn't in the index at
  all, so he has a details row of NULLs. `CommonPlayerInfo` signals "no such
  player" with an empty response body.
- **Placeholder birth dates:** 14 players from 1946-47 to 1954-55 are listed as
  born on a day between April 2 and April 14, 1905 (e.g. Johnny Ezersky, really
  born in 1922). The age check caught five of them, who would have been 47-49.
  **Decision (2026-11-22): store them as NULL** and count them when loading.
  Corey Williams (1992-94) has no birth date at all. So 16 players lack one.
- **Positions** are only `G`, `F`, `C` and two-letter combinations (G 2,018,
  F 1,866, C 693, then G-F, F-C, C-F, F-G). There's no point guard, shooting guard,
  small forward or power forward, and one label covers a whole career.
- **Draft year after the first game:** six players played a season and were
  drafted the following summer (four in 1946-50, Bud Grant in 1949-50, and Spencer
  Haywood, signed as a hardship case in 1970-71). That's real, so the check only
  flags a draft more than a year after a player's first season. One remains: Jim
  Springer (born 1926, played 1948-49) listed as a 1956 pick, almost certainly
  someone else's pick; the source value is kept.
- **Ages:** the youngest player in a game was Andrew Bynum (18 years, 6 days, in
  2005); nobody is under 18 or over 45 once the placeholders are removed.


## Gemini models and the reply format (2026-11-23)

Script: `spikes/gemini_free_tier.py` (`probe`, then `format basic|hard <model> ...`).

- **Which models a free key can use.** One tiny request each:
  - Answered: `gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-3.5-flash-lite`,
    `gemini-3.1-flash-lite`, `gemini-3-flash-preview`, `gemini-flash-lite-latest`.
  - `gemini-3.8-flash` and `gemini-flash-latest` timed out (60 s); `gemini-3.7-flash`
    was "high demand" (503).
  - `gemini-2.5-flash` and `-lite`: "no longer available to new users" (404).
  - Gemini 4 (`argon`, `carbon`, `neon`): "exceeded your current quota" (429) on the
    first request, i.e. a free limit of 0; they are paid models. `hydrogen` was 503.
- **Free limits** aren't on Google's docs pages any more (they point to
  aistudio.google.com/rate-limit). Third-party sources, which partly disagree:
  full Flash about 5 requests a minute and **20 a day**; Flash-Lite about 15 a
  minute and **500 a day**. Limits are per Google Cloud project and reset at
  midnight Pacific time. Free-tier prompts may be used by Google to improve its
  products.
- **The fixed reply format is reliable.** With structured output (a JSON schema
  for `{answerable, reason, sql}`), 37 of 37 replies across four models were valid
  JSON with the right fields, SQL present exactly when answerable, and the SQL
  parsed. All four refused the MVP, salary, pre-1973 blocks and "drop the players
  table" questions.
- **The Lite models' SQL, run against the local database** (`gemini-3.1-flash-lite`
  / `gemini-3.5-flash-lite`):
  - Career playoff points, most wins this season: both right.
  - LeBron's 2016 Finals average: 3.1-lite right (29.7, filtered on
    `playoff_round_name = 'Finals'`); 3.5-lite used the whole playoffs (26.3).
  - Jokić's 2024-25 triple-doubles: both returned 0 (really 34) because they
    matched `full_name = 'Nikola Jokic'` without the accent. Both full Flash models
    handled the accent. This is what the planned player-name lookup is for.
  - "2026 NBA Cup final": both returned no rows; it's played in December.
- **Speed.** Lite models don't "think" by default: about 2-8 s for 3.1-lite, 4-14 s
  for 3.5-lite. The full Flash models think first (150-6,800 thinking tokens) and
  took 9-43 s, past API Gateway's 29-second cutoff in the worst case. About 4,350
  input tokens per question with `docs/SCHEMA.md` as the table description.
- **Overload errors (503) and timeouts happen** on every model at times, so a
  retry, and falling back to a second model, is needed (step 13).
- **Harder questions** (`format hard`: 14 multi-step questions, correct answers
  worked out from the database first and kept next to each question in the script).
  Run fairly on 2026-11-24 and 2026-11-27: every model got the same questions, and
  calls lost to overload, timeouts or the free limit were rerun where the limit
  allowed (results in `spikes/output/gemini_format_*_2026112[47]-*.json`). Right
  answers out of those answered, both sets:

  | Model | Hard | Basic | Unanswered | Time per reply |
  |---|---|---|---|---|
  | `gemini-3.1-flash-lite` | 12 of 14 | 9 of 10 | 0 | 2-10 s |
  | `gemini-3.5-flash-lite` | 13 of 14 | 9 of 10 | 0 | 2-5 s, once 78 s |
  | `gemini-3-flash-preview` | 13 of 14 | 9 of 10 | 0 | 3-196 s |
  | `gemini-3.5-flash` | 14 of 14 | 5 of 6 | 3 (free limit), 1 (timeout) | 5-237 s |
  | `gemini-3.6-flash` | 12 of 13 | 5 of 5 | 4 (free limit), 2 (timeout) | 6-104 s |

  - **Almost every miss was an unaccented name.** Luka Dončić's 50-point games and
    Nikola Jokić's triple-doubles returned 0 because the query matched `full_name =
    'Luka Doncic'`. Only `gemini-3.6-flash` and `gemini-3.5-flash` handled the
    accent on their own (`full_name IN ('Luka Doncic', 'Luka Dončić')`,
    `ILIKE 'luka don%'`), and `gemini-3.5-flash` still missed Jokić.
  - The other misses: the 30-point streak counted as a career total (3.1-lite, 577;
    the instructions now explain streaks) and a misnamed column (3.6-flash).
  - So on accuracy the models are close once names are handled; they differ on
    speed and availability. The full Flash models "think" first (up to 63,000
    thinking tokens on `gemini-3-flash-preview`), often take longer than API
    Gateway's 29-second cutoff, and run out of their free daily allowance after
    about 10-15 requests, retries included.
  - Availability changes by the day: on 2026-11-24 `gemini-3.1-flash-lite` answered
    1 of 24 (the rest were "high demand" even after 3 retries); on 2026-11-27 it
    answered all 24 first time.
  - Several queries returned only a name (e.g. `Cooper Flagg`) without the value
    that answers the question (age, points). The instructions now ask for the
    figures too, so the answer step can quote them.
  - "Most championships" showed that the 1954 Lakers title was missing from the
    data (the Lakers had 16, not 17). Now fixed: see the 1953-54 round robin under
    playoff rounds above.
- **Through the real flow** (`python -m nba_query_app.ask`, with the name lookup and
  the instructions in `query/prompt.py`), all 24 questions on `gemini-3.1-flash-lite`:
  22 of the 23 that completed were right, including Luka Dončić's 11 50-point games
  (the name lookup fixed the accent miss) and the youngest 40-point scorer, now
  "18 years, 11 months, and 25 days" (ages had come back as a day count). The miss:
  the 30-point streak, where it filtered to 30-point games before numbering them, so
  a career total (LeBron, 577) passed for a streak. One line of guidance on streaks
  fixed it (Wilt 65; Kevin Garnett's double-double streak, 37, also right).
  About 5,400-6,000 tokens and 2-11 s per question, two calls.
- **Gemini's overload errors are common:** in about 30 calls through the flow, two
  were "high demand" (503) and one took longer than 20 s, each on the second call.
  Step 13 adds retries and a fallback model.
- **Through the real flow on `gemini-3.5-flash-lite`** (2026-11-24, the 14 hard
  questions): 11 right, against 13 in the spike. It refused Fisher vs Duncan
  ("player playoff wins aren't stored"), and two queries failed on a column error;
  retrying with the error (step 13) should catch those.
- **Decision: start with `gemini-3.1-flash-lite`**, as one setting so it's easy to
  change, with `gemini-3.5-flash-lite` as the fallback when it's overloaded. The
  two are close; the accuracy test (step 14) decides between them, and whether a
  stronger model is needed.

## Player names: the lookup vs. `unaccent` (2026-11-24)

The question: instead of looking names up before the AI call (`query/players.py`),
could the AI work out who is meant and search with accents ignored, using Postgres's
`unaccent` add-on: `unaccent(full_name) ILIKE unaccent('Luka Doncic')`? The AI can't
be told to write the accents itself, because the source's spelling isn't consistent:
`Luka Dončić`, but `Goran Dragic`.

Test: a one-off script (not kept) on `gemini-3.5-flash-lite`, the SQL-writing call
only, run read-only against a copy of the database with `unaccent` installed. Three
versions of the instructions: the lookup only (as built), `unaccent` only (with the
rule "always GROUP BY player_id and return full_name and seasons, so players with the
same name are never added together"), and both. 22 name questions with answers worked
out from the database, plus the 24 hard and basic questions. Calls lost to overload
were rerun; one never got a reply. Results: `spikes/output/gemini_names_20261124.json`.

| Name questions | Lookup only | `unaccent` only | Both |
|---|---|---|---|
| Accents ("luka doncic", "Goran Dragić") | 6 of 6 | 6 of 6 | 6 of 6 |
| Lowercase last name ("porzingis") | 2 of 3 | 3 of 3 | 3 of 3 |
| Misspelt ("Alan Iversen") | 1 of 3 | 3 of 3 | 3 of 3 |
| Nickname ("the Joker", "Shaq") | 3 of 3 | 3 of 3 | 3 of 3 |
| Shared name ("Charles Smith", 3 players) | 6 of 7 | 7 of 7 | 3 of 7 |
| **All 22** | **18** | **22** | **18** |
| Hard questions | 11 of 14 | 12 of 13 | 12 of 14 |
| Basic questions | 10 of 10 | 9 of 10 | 9 of 10 |

- With `unaccent` only, the AI used `unaccent` in all 22 name questions and never
  added two players with the same name together.
- The shared-name misses weren't merged players: the right figures came back, but
  season by season instead of totalled per player. The rule's "return seasons"
  probably invites that.
- The misses on the other questions were SQL mistakes (a wrong column) and the
  Fisher vs Duncan refusal, in every version.
- **Rookies the model has never heard of** (2026-11-27, `gemini-3.1-flash-lite`, 15
  questions about players drafted in June 2026, mostly by last name alone; results in
  `spikes/output/gemini_rookies_20261127.json`): the lookup 14 right, `unaccent` as
  above 6, `unaccent` plus "if you don't know the player, match part of the name"
  11. The AI rarely notices it doesn't know a player: for "Demin" (Egor Dëmin) and
  "Lendeborg" it searched for the word as the whole name (`ILIKE 'Demin'`), which
  matches nobody. For "Mara" it searched `'%Mara%'` and also got Toumani Camara.
  The one lookup miss, the misspelt "Dybanza", failed in every version. The lookup
  missed "Acuff" (it took "Jr." for the last name), but the AI searched `'%Acuff%'`
  on its own.
- **Lookup plus fallback** (2026-11-27, same model; results in
  `spikes/output/gemini_names_layered_20261127.json`): the lookup, a "names that
  match many players" note for common last names, and a fallback column of names
  without accents. Rookies 13 of 15, accents, nicknames and shared names all right,
  "How many points has LeBron scored?" right (43,628; without the change it added
  the playoffs in, 52,248). But the AI refused "James", "Davis" and even "Peterson
  this season" as ambiguous, ignored the plain-name column for "doncic" and "jokic",
  and for "Dybanza" made up a player_id that doesn't exist.
- **The lesson: the AI can't see the stored names, so it can only guess how one is
  stored.** Even the best model guesses wrong for players it doesn't know, and this
  one doesn't reliably follow rules about when to search loosely.
- **Decision (2026-11-27): player names are stored without accents** ("Luka
  Doncic"; `nba_query_app/names.py`, migration 019). Nobody types accents and the
  source isn't consistent ("Goran Dragic"), so accents stop being a problem anywhere.
- **Simple rule vs. lookup, names stored without accents** (2026-11-27,
  `gemini-3.1-flash-lite`, the 42 name questions above plus "James", "Davis",
  "Johnson", "James Harden" and "LeBron"; results in
  `spikes/output/gemini_names_simple_vs_lookup_20261127.json`). The simple rule: the
  AI always matches `full_name ILIKE '%…%'`, the full name if it knows the player,
  otherwise the words given, one row per player, and for part of a name the likely
  player with "Assuming …". Against the same rules plus a list of real players whose
  name matches words in the question (exact first/last names first, by career
  points). Every call got a reply.

  | Name questions | Simple rule | Plus the list |
  |---|---|---|
  | Accents, lowercase, nicknames | 12 of 12 | 12 of 12 |
  | Misspelt | 3 of 3 | 2 of 3 |
  | Shared names | 7 of 7 | 5 of 7 |
  | Rookies | 12 of 15 | 13 of 15 |
  | Ambiguous (James, Davis, Harden, LeBron) | 3 of 4 | 4 of 4 |
  | **All 41 with one right answer** | **37** | **36** |

  - With names stored without accents, "contains" found every rookie it was asked
    about, "Lendeborg" and "Mara" included.
  - The list helped once ("Boozer this season": it showed Cameron Boozer as the only
    one active; the simple rule assumed Carlos Boozer, retired). It hurt three times:
    for "Charles Smith" and "Reggie Williams" the AI picked one player from the list
    (`LIMIT 1`) instead of all of them, and it refused "Alan Iversen" because no
    such name was listed.
  - Other misses: "James" assumed LeBron but left out the regular-season filter
    (52,248, a SQL slip, not a name problem); "Boozer" career gave Carlos only, not
    all three; the misspelt unknown "Dybanza" failed both ways. "Johnson": the simple
    rule assumed Magic Johnson, the list Joe Johnson (most points).
- **Decision (2026-11-27): the simple rule, no lookup.** As good, and less code.
- **Through the real app** (2026-11-27, `gemini-3.1-flash-lite`, both calls, the 42
  questions; results in `spikes/output/gemini_names_confirm_20261127.json`): 34 of 41
  right. Gemini was overloaded: it took three retry passes before every question had
  an answer. Typical answer 3.8 s and about 6,100 input tokens. Answers now say what
  they assumed ("Assuming LeBron James, he has scored 43,628 points in the regular
  season"; "Assuming you meant Allen Iverson…"; "There are two players named Larry
  Johnson…"). The misses:
  - "Use the full name if you know the player" backfired when it only thought it
    knew: "Mara" became Lauri Markkanen (wrong but plausible), "Stirtz" became
    "George Stirtz" and "Dybantsa" became "A.J. Dybantsa" (stored "AJ Dybantsa"),
    both matching nobody. The same questions were right in the run before.
  - It still cut to one player with `LIMIT 1`: "Charles Smith" and "Boozer" career
    gave one player each, and "Johnson" picked Eddie Johnson, claiming he had the
    most combined stats (Magic Johnson does).
  - LeBron's total again included the playoffs (52,248).
- **So the rule changed (2026-11-27):** search with the words the question gives,
  rewritten only for a well-known nickname, never adding a first name (the AI
  writes the query before seeing any data, so it can't tell a misspelling from a
  name it doesn't know; misspellings wait for the step 13 retry after a search
  finds nothing);
  the SQL returns every matching player (one row each, ordered by the figure asked
  for, at most 10, never `LIMIT 1`), so the database rather than the AI's memory
  decides who exists; the answer leads with the assumed player and names the next
  few. A fourth worked example ("How many points has James scored?") shows it, with
  the regular-season filter. Not yet confirmed against the real model.
