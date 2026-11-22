# Database schema

Everything the app answers from lives in one Postgres schema (a folder of tables),
`nba`. This page describes it for people. The AI gets the same information from
the column comments in `migrations/`, which say the same things in fewer words.

**What's in it:** every official, competitive NBA game from 1946-47 (the BAA, the
NBA's first league) to today: regular season, NBA Cup final, play-in and playoffs.
Exhibitions such as the All-Star game are left out. Data comes from stats.nba.com.

**Two rules that apply everywhere:**
- **NULL means "not recorded", never 0.** Early seasons lack many stats (no blocks
  before 1973-74, no 3-pointers before 1979-80, minutes missing in the earliest
  years). A 0 means the player really had none. When a stat became reliable is
  computed in `nba.stat_tracked_since`.
- **IDs are NBA.com's own.** A team keeps its `team_id` when it moves or is renamed
  (the Seattle SuperSonics and Oklahoma City Thunder share one). Game IDs are text
  with leading zeros (`0022600003`).

## How the tables fit together

```
teams ──┬──────────────< games >─────────────┐
        │                  │                 │
        │      player_game_stats      team_game_stats
        │      (player, team, game)   (team, game)
        │                  │
players ┴──────────────────┘
   └── player_details (one row per player)

Views (saved queries, always in step with the tables):
  player_season_stats   season totals per player
  playoff_series        one row per playoff series
  stat_coverage         how completely each stat was recorded, per season
  stat_tracked_since    the season each stat became fully recorded
```

- A box score line (`player_game_stats`) joins to `games` on `game_id` for the date
  and season, to `players` on `player_id`, and to `teams` on `team_id`.
- A team's game (`team_game_stats`) joins to `games` on `game_id` and to `teams` on
  `team_id`. Use it for scores and team totals rather than adding up player rows
  (see "Known data gaps").

## Tables

### `nba.games`
One row per game.

| Column | Meaning |
|---|---|
| `game_id` | NBA.com game ID, e.g. `0022600003`. The key. |
| `game_date` | Local date the game was played. |
| `season` | Season label, e.g. `2026-27` for the season starting in October 2026. |
| `season_type` | `Regular Season`, `NBA Cup Final` (from 2023-24, not counted in regular-season stats), `Play-In` (from 2019-20) or `Playoffs`. |
| `home_team_id`, `away_team_id` | The home and away teams. Both NULL at a neutral site. |
| `neutral_site` | True for games at a neutral venue (Mexico City, Paris, the Las Vegas NBA Cup semifinals). Older neutral-site games are listed with a home team in the source. |
| `nba_cup_round` | NBA Cup round: `Group`, `Quarterfinal`, `Semifinal` or `Final`. NULL for non-Cup games. Group, quarterfinal and semifinal games are also regular-season games. |
| `playoff_round` | Playoff round number: 1 = that season's first round, up to the Finals. The number of rounds varies (4 since 1983-84). |
| `playoff_round_name` | `First Round`, `Conference Semifinals`, `Conference Finals` or `Finals`; before 1970-71, `Division Semifinals` and `Division Finals`. |
| `playoff_series_game` | Game number within the playoff series (1 = first game). |

The three playoff columns are NULL for non-playoff games, and for the 1953-54
playoffs, which began with a round robin rather than series.

### `nba.player_game_stats`
One row per player per team per game they played in: the box score line. A player
has two rows in one game only in the rare case they played for both teams (the
protested 76ers–Nets game of 1978-11-08, finished after a trade).

| Column | Meaning |
|---|---|
| `game_id`, `player_id`, `team_id` | The game, the player and the team they played for. Together the key. |
| `team_abbreviation` | The team's abbreviation at the time (e.g. `NJN` before the Nets moved). |
| `opponent_team_id` | The team they played against. |
| `is_home` | True if their team was at home, false if away, NULL at a neutral site. |
| `won` | True if their team won. |
| `minutes` | Minutes played, rounded to a whole number. Often NULL before the 1980s. |
| `pts` | Points. Always recorded. |
| `fgm`, `fga` | Field goals made and attempted, including 3-pointers. `fgm` is always recorded. |
| `fg3m`, `fg3a` | 3-pointers made and attempted. NULL before 1979-80 (no 3-point line). |
| `ftm`, `fta` | Free throws made and attempted. `ftm` is always recorded. |
| `oreb`, `dreb`, `reb` | Offensive, defensive and total rebounds. |
| `ast`, `stl`, `blk`, `tov`, `pf` | Assists, steals, blocks, turnovers, personal fouls. |
| `plus_minus` | Point difference while on court. From 1996-97. |

### `nba.team_game_stats`
One row per team per game: the final score and team totals, from the source's own
team-level record.

| Column | Meaning |
|---|---|
| `game_id`, `team_id` | The game and the team. Together the key. |
| `team_abbreviation` | The team's abbreviation at the time. |
| `opponent_team_id` | The team they played against. |
| `is_home` | True if at home, false if away, NULL at a neutral site. |
| `won` | True if this team won; always the team with more points. |
| `pts`, `opponent_pts` | This team's and the opponent's final score. |
| `minutes` | Total player minutes: 240 for regulation plus 25 per overtime, so `(minutes - 240) / 25` is the number of overtimes. NULL in early seasons. |
| `fgm`, `fga`, `fg3m`, `fg3a`, `ftm`, `fta` | Team shooting, as for players. |
| `oreb`, `dreb`, `reb`, `ast`, `stl`, `blk`, `tov`, `pf` | Team totals, as for players. |

### `nba.players`
Every player who has appeared in a game.

| Column | Meaning |
|---|---|
| `player_id` | NBA.com player ID. The key. |
| `full_name` | Full name as NBA.com lists it, e.g. `LeBron James`. Two players can share a name (there are two Eddie Johnsons). |
| `name_as_of` | Bookkeeping for the loader: the date of the latest game the name was taken from. |

### `nba.player_details`
One row per player: biographical details as NBA.com lists them today. Each value
is a single figure for the whole career, not per season.

| Column | Meaning |
|---|---|
| `player_id` | The player. The key. |
| `position` | `G`, `F`, `C`, or two letters with the main one first (`G-F`, `F-G`, `F-C`, `C-F`). Only guard, forward and center: NBA.com has no point guard, shooting guard, small forward or power forward labels. One label for the whole career, so it can mislead for players who changed roles. |
| `height_inches` | Height in inches (82 = 6 ft 10 in). |
| `weight_lbs` | Weight in pounds, as last listed. |
| `birth_date` | Date of birth. Age on a game day is `age(game_date, birth_date)`. NULL for 16 players (see "Known data gaps"). |
| `college` | Last school before the NBA: usually a college, sometimes a high school or a club abroad. |
| `country` | Country, e.g. `USA`, `France`. |
| `draft_year`, `draft_round`, `draft_number` | The draft the player was picked in, the round and the overall pick. NULL if undrafted or not recorded (the draft began in 1947); about 72% of players have a draft year. Early territorial picks have a year but no round. Only the year is stored, so age at the draft is approximate (it can be off by a year); age in a game is exact. |
| `birth_date_checked` | Bookkeeping for the loader: true once the birth date has been looked up. |

### `nba.teams`
Every franchise, with its most recent abbreviation and name.

| Column | Meaning |
|---|---|
| `team_id` | NBA.com team ID. Stays the same when a franchise moves or is renamed. The key. |
| `abbreviation` | Most recent abbreviation, e.g. `OKC`. Historical abbreviations are on the game rows. |
| `name` | Most recent full name, e.g. `Oklahoma City Thunder`. |
| `name_as_of` | Bookkeeping for the loader: the date of the latest game the name was taken from. |

### `nba.meta`
A single row describing the loaded data.

| Column | Meaning |
|---|---|
| `id` | Always true; makes sure there's only one row. |
| `data_version` | Goes up whenever a load changes something. The answer cache is cleared when it changes. |
| `latest_game_date` | Date of the newest game loaded. |
| `updated_at` | When the data last changed. |

## Views

### `nba.player_season_stats`
Season totals: one row per player per season per season type, with all their teams
combined. Calculated from `player_game_stats`, so the two always agree.

| Column | Meaning |
|---|---|
| `player_id`, `full_name` | The player. |
| `season`, `season_type` | The season and type. Regular season and playoffs are separate rows. |
| `games` | Games played. |
| `team_abbreviations` | Every team they played for, alphabetical (e.g. `HOU, LAL` after a trade). |
| `minutes`, `pts`, `fgm`, `fga`, `fg3m`, `fg3a`, `ftm`, `fta`, `oreb`, `dreb`, `reb`, `ast`, `stl`, `blk`, `tov`, `pf`, `plus_minus` | Totals. A total is NULL unless the stat was recorded in every one of the player's games that season, so it never undercounts. |

### `nba.playoff_series`
One row per playoff series (every season except 1953-54).

| Column | Meaning |
|---|---|
| `season`, `playoff_round`, `playoff_round_name` | When, and which round. The champion is the winner of the `Finals` row. |
| `winner_team_id`, `winner_abbreviation` | The series winner, with its abbreviation at the time. |
| `loser_team_id`, `loser_abbreviation` | The loser. |
| `winner_wins`, `loser_wins` | The series score, e.g. 4 and 3. |
| `games` | Games played in the series. |
| `first_game_date`, `last_game_date` | When the series started and ended. |

During the current playoffs a series may still be in progress: then "winner" is the
team currently ahead.

### `nba.stat_coverage`
How completely each stat was recorded: one row per season per stat.

| Column | Meaning |
|---|---|
| `season`, `stat` | The season, and the column name in `player_game_stats` (e.g. `blk`). |
| `player_rows` | Box score lines that season. |
| `recorded_rows` | Lines where the stat is not NULL. |
| `pct_recorded` | `recorded_rows` as a percentage. |

### `nba.stat_tracked_since`
When each stat was recorded: one row per stat.

| Column | Meaning |
|---|---|
| `stat` | Column name in `player_game_stats`. |
| `first_recorded_season` | First season with any value. Before it, questions about the stat must be refused. |
| `tracked_since` | First season from which every season has the stat in at least 98% of rows. Before it (but after the first recorded season), answers must say the figures are incomplete. |

As of November 2026:

| Stat | First recorded | Fully recorded from |
|---|---|---|
| Points, FG made, FT made | 1946-47 | 1946-47 |
| FT attempted | 1946-47 | 1948-49 |
| Assists | 1946-47 | 1975-76 |
| Rebounds | 1950-51 | 1975-76 |
| FG attempted, minutes | 1946-47, 1950-51 | 1976-77 |
| 3-pointers made | 1979-80 | 1979-80 |
| Personal fouls | 1946-47 | 1980-81 |
| Blocks, steals, turnovers, off./def. rebounds, 3-pointers attempted | 1962-63 to 1979-80 | 1983-84 |
| Plus-minus | 1996-97 | 1996-97 |

## Known data gaps

The data-quality checks (`python -m nba_query_app.quality`) report these after each
load. Details are in `spikes/FINDINGS.md`.

- **Players the source lost.** stats.nba.com has a few players' box score lines
  without a player ID, so their rows are missing: Loy Vaught (Clippers, 1990-91 to
  1995-96), one Nets player in 1995-96, and a few other games. Team scores are
  still right in `team_game_stats`.
- **Old box scores that don't add up.** In about 1,180 team-games, almost all before
  1996-97, player points don't add up to the team's score. The source values are
  kept.
- **Wrong minutes** in about 70 team-games in 2005-06 and 2006-07.
- **The 1953-54 playoffs** have no rounds (a round-robin format).
- **Missing birth dates** for 16 players: 14 from 1946-47 to 1954-55, for whom
  NBA.com lists a placeholder date (stored as NULL), plus Bobby Watson (1952-53,
  missing from NBA.com's player list, so all his details are NULL) and Corey
  Williams (1992-94). Age questions leave them out.
- **Single career values.** Position, height and weight in `player_details` are one
  value per player, not per season.
- **Three positions only.** Guard, forward and center; there's no point guard or
  shooting guard (and so on) in the source.
- **Small differences from official totals** in a few career figures (e.g. Wilt
  Chamberlain's career points: 31,417 here, 31,419 officially).

## Common questions and where the answer is

- **A player's season or career totals:** `player_season_stats`, filtered on
  `season_type`. Sum seasons for a career.
- **Single-game feats** ("most points in a game", "triple-doubles"):
  `player_game_stats` joined to `games` and `players`.
- **Scores, margins, team records, overtime games:** `team_game_stats` joined to
  `games`.
- **Champions and playoff series:** `playoff_series`; the champion is the winner of
  the `Finals` row.
- **Ages, positions, heights, draft picks, colleges:** `player_details` joined to
  `players` (and to `games` for age on a game day).
- **Is this stat available for that season?** `stat_tracked_since`.
