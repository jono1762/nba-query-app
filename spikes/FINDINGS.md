# Spike findings

Experiments and data investigations. The first sections are step 1 of `docs/PLAN.md`.

Run on 2026-11-18 from a home connection (WSL2 on Windows).

## stats.nba.com (history source): works
- `LeagueGameLog(player_or_team_abbreviation="P")` returns **a whole season in one
  request**, in under a second, with no special headers needed.
  - 2000-01: 23,973 rows, 1,189 games (4.6 MB of JSON)
  - 2026-27 so far: 4,721 rows, 210 games, latest game 2026-11-17 (yesterday)
- So the full 2000-01 to now backfill is about 27 requests and roughly 650k rows.
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

## Full backfill (2026-11-20)
- 27 seasons (2000-01 to 2026-27), regular season and playoffs: 54 requests in
  116 seconds with a 1-second pause between them. No errors and no retries needed.
- Result: 699,228 player-game rows, 33,636 games, 2,641 players, 30 teams.
  The whole database is 140 MB, within Neon's free 0.5 GB.
- Sanity checks passed:
  - 1,230 regular-season games in a normal 30-team season (1,189 in 2000-01
    with 29 teams).
  - Shortened seasons show fewer games (2011-12: 990, 2019-20: 1,059).
  - Known records are present (Kobe 81 on 2006-01-22, Dončić 73 on 2024-01-26).
- **11 neutral-site games** were detected. They match known neutral venues: Mexico
  City (MIA-WAS 2024-11-02, DAL-DET 2025-11-01), the Las Vegas NBA Cup
  semifinals (2024-12-14), and Paris (IND-SAS, January 2025).
  - Earlier international games are listed with a normal home team, so they
    aren't flagged.

## Full history from 1946-47, plus play-in (2026-11-20)
Decision: load every season from 1946-47 (the BAA's first season) and add play-in
games. The 2000-01 to now load above was the first step.

- **Load:** 81 seasons × regular season, play-in (from 2019-20) and playoffs. That's
  170 requests in about 5 minutes from an empty database.
- **Result:** 1,487,560 player-game rows, 73,392 games (37 play-in), 5,159
  players, 45 franchises. The database is **305 MB**, about 60% of Neon's free 0.5 GB.
- **Missing stats are NULL in the source**, never 0, with one exception: early
  seasons report `MIN = 0` for every player. How complete each stat is, by season
  (share of rows with a value):

  | Season | Minutes | Rebounds | Assists | Steals | Blocks | Turnovers | 3PA |
  |---|---|---|---|---|---|---|---|
  | 1946-47 | 0% | 0% | 2% | 0% | 0% | 0% | 0% |
  | 1960-61 | 60% | 69% | 38% | 0% | 0% | 0% | 0% |
  | 1973-74 | 88% | 92% | 92% | 28% | 31% | 0% | 0% |
  | 1979-80 | 99% | 99% | 98% | 72% | 82% | 65% | 85% |
  | 1985-86 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |

  Coverage rises gradually and is often partial within a season. Step 7 will
  compute exact "fully recorded from" seasons per stat.

- **Errors in old box scores, and how they're handled** (each found in the real
  data and covered by a test):
  - *Attempts lower than makes* (a 1946-47 player with 1 FT made, 0 attempted):
    the attempts become NULL.
    - Checked whether the column might mean *misses* instead. It doesn't:
      - These rows are rare: at most 19 of about 6,000 per season, none after 1966-67.
      - The source's own percentage treats the value as attempts (Bob Feerick
        1947-48: 4 FT made, 1 attempted, shown as 400%).
      - League free-throw percentage only matches history under the attempts
        reading: 1951-52 is 73.5% as attempts vs 42.4% as misses, and the
        historical figure is about 73%.
    - So they're recording errors. Some may be misses typed into the wrong
      column, but nothing proves which, so no value is guessed.
  - *W/L missing on a single player row* (10 rows in 1959-60): filled in from
    teammates.
  - *A couple of rows with the wrong home/away label* (1967-68 SFW at SDR: 10 rows
    say away, 2 say home): majority vote per team.
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
- **Neutral-site games:** still 11, all from 2024-25 on. Earlier neutral-venue
  games, common in the 1950s-60s, are listed with a normal home team in the
  source, so they aren't flagged.

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
