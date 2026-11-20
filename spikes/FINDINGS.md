# Phase 0 spike findings

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
- The history download (Phase 2) works as planned.
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
