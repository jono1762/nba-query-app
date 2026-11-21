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
  players, 45 franchises. The database is **305 MB**, about 60% of Neon's free 0.5 GB.
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
  - *A couple of rows with the wrong home/away label* (1967-68 SFW at SDR: 10 rows
    say away, 2 say home; those 2 are the only ones in history): majority vote per team.
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

- **Cleanup rules, exact totals for the full history:** 11 ghost rows dropped, 2
  home/away labels outvoted, 127,544 zero-minute rows made "not recorded", 115
  impossible attempt values made "not recorded", 25 missing W/L values filled in.
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
- **Player points that don't match the team score (1946-47 to 1989-90).** 262 games
  where the winner's player rows add up to fewer points than the loser's. Checked
  against the source's team-level log for 1980-81: the W/L is right and the player
  rows are off (e.g. 0028000198: Atlanta scored 88, but its player rows add up to
  108). Nothing shows which row is wrong, so the source values are kept.
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
- **Possible next check:** the source's team-level log (one request per season)
  has each team's final score. Storing scores would catch every game whose player
  rows don't add up, not only the ones where that flips the winner, and would let
  the AI answer questions about final scores.
