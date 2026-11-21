-- Minutes weren't recorded for some players until the early 1980s, not only in the
-- earliest seasons (see ingest/normalize.py: a 0 on a team whose minutes fall well
-- short of the game length is stored as NULL).

COMMENT ON COLUMN nba.player_game_stats.minutes IS
    'Minutes played, rounded to a whole number. NULL if not recorded (common before the '
    '1980s; nba.stat_tracked_since says from when minutes are fully recorded).';
