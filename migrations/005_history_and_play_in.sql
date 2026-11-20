-- Support the full history (from 1946-47) and play-in games.
--
-- Many stats weren't recorded in early seasons (e.g. no blocks or steals before
-- 1973-74, no 3-pointers before 1979-80, minutes missing in the earliest years).
-- Those are stored as NULL, meaning "not recorded", which is different from 0.
-- Points, field goals made and free throws made are always recorded and stay required.

ALTER TABLE nba.player_game_stats
    ALTER COLUMN minutes DROP NOT NULL,
    ALTER COLUMN fga DROP NOT NULL,
    ALTER COLUMN fg3m DROP NOT NULL,
    ALTER COLUMN fg3a DROP NOT NULL,
    ALTER COLUMN fta DROP NOT NULL,
    ALTER COLUMN oreb DROP NOT NULL,
    ALTER COLUMN dreb DROP NOT NULL,
    ALTER COLUMN reb DROP NOT NULL,
    ALTER COLUMN ast DROP NOT NULL,
    ALTER COLUMN stl DROP NOT NULL,
    ALTER COLUMN blk DROP NOT NULL,
    ALTER COLUMN tov DROP NOT NULL,
    ALTER COLUMN pf DROP NOT NULL;

COMMENT ON TABLE nba.player_game_stats IS
    'One row per player per game they played in (box score), from 1946-47. Join to nba.games '
    'for date and season. A NULL stat means it was not recorded for that game (common in early '
    'seasons), which is different from 0.';
COMMENT ON COLUMN nba.player_game_stats.minutes IS
    'Minutes played, rounded to a whole number. NULL if not recorded (earliest seasons).';
COMMENT ON COLUMN nba.player_game_stats.fga IS
    'Field goals attempted (includes 3-pointers). NULL if not recorded.';
COMMENT ON COLUMN nba.player_game_stats.fg3m IS
    '3-point field goals made. NULL if not recorded (no 3-point line before 1979-80).';
COMMENT ON COLUMN nba.player_game_stats.fg3a IS
    '3-point field goals attempted. NULL if not recorded (no 3-point line before 1979-80).';
COMMENT ON COLUMN nba.player_game_stats.fta IS 'Free throws attempted. NULL if not recorded.';
COMMENT ON COLUMN nba.player_game_stats.oreb IS 'Offensive rebounds. NULL if not recorded.';
COMMENT ON COLUMN nba.player_game_stats.dreb IS 'Defensive rebounds. NULL if not recorded.';
COMMENT ON COLUMN nba.player_game_stats.reb IS 'Total rebounds. NULL if not recorded.';
COMMENT ON COLUMN nba.player_game_stats.ast IS 'Assists. NULL if not recorded.';
COMMENT ON COLUMN nba.player_game_stats.stl IS 'Steals. NULL if not recorded.';
COMMENT ON COLUMN nba.player_game_stats.blk IS 'Blocks. NULL if not recorded.';
COMMENT ON COLUMN nba.player_game_stats.tov IS 'Turnovers. NULL if not recorded.';
COMMENT ON COLUMN nba.player_game_stats.pf IS 'Personal fouls. NULL if not recorded.';

-- Play-in tournament games (from 2019-20) are their own season type.
ALTER TABLE nba.games DROP CONSTRAINT games_season_type_check;
ALTER TABLE nba.games ADD CONSTRAINT games_season_type_check
    CHECK (season_type IN ('Regular Season', 'Play-In', 'Playoffs'));
COMMENT ON COLUMN nba.games.season_type IS
    'One of ''Regular Season'', ''Play-In'' (play-in tournament, from 2019-20) or ''Playoffs''.';
COMMENT ON SCHEMA nba IS
    'NBA (and its 1946-49 predecessor, the BAA) player game statistics from stats.nba.com, 1946-47 to today.';
