-- How completely each stat was recorded, by season, and the season each stat
-- became fully recorded. Calculated from the data rather than typed-in dates,
-- because coverage in the source ramps up gradually and unevenly (e.g. blocks:
-- 32% of box scores in 1973-74, 82% in 1979-80, all from 1983-84). Used to decide
-- when a question must be refused ("blocks weren't tracked in 1962") or caveated
-- ("blocks counted from 1973-74").
--
-- "Fully recorded" means at least 98% of player rows have a value. Not 99%:
-- 1984-85 sits at about 98.5% for most stats because a few players have partial box
-- scores all season, and 99% would push every stat's start to 1985-86.

CREATE VIEW nba.stat_coverage AS
WITH per_season AS (
    SELECT
        g.season,
        count(*) AS player_rows,
        count(s.minutes) AS minutes,
        count(s.pts) AS pts,
        count(s.fgm) AS fgm,
        count(s.fga) AS fga,
        count(s.fg3m) AS fg3m,
        count(s.fg3a) AS fg3a,
        count(s.ftm) AS ftm,
        count(s.fta) AS fta,
        count(s.oreb) AS oreb,
        count(s.dreb) AS dreb,
        count(s.reb) AS reb,
        count(s.ast) AS ast,
        count(s.stl) AS stl,
        count(s.blk) AS blk,
        count(s.tov) AS tov,
        count(s.pf) AS pf,
        count(s.plus_minus) AS plus_minus
    FROM nba.player_game_stats s
    JOIN nba.games g USING (game_id)
    GROUP BY g.season
)
SELECT
    p.season,
    v.stat,
    p.player_rows::integer AS player_rows,
    v.recorded::integer AS recorded_rows,
    round(100.0 * v.recorded / p.player_rows, 1) AS pct_recorded
FROM per_season p
CROSS JOIN LATERAL (
    VALUES
        ('minutes', p.minutes),
        ('pts', p.pts),
        ('fgm', p.fgm),
        ('fga', p.fga),
        ('fg3m', p.fg3m),
        ('fg3a', p.fg3a),
        ('ftm', p.ftm),
        ('fta', p.fta),
        ('oreb', p.oreb),
        ('dreb', p.dreb),
        ('reb', p.reb),
        ('ast', p.ast),
        ('stl', p.stl),
        ('blk', p.blk),
        ('tov', p.tov),
        ('pf', p.pf),
        ('plus_minus', p.plus_minus)
) AS v (stat, recorded);

COMMENT ON VIEW nba.stat_coverage IS
    'How completely each stat was recorded in each season: one row per season per stat, '
    'counting player rows in nba.player_game_stats (all season types) that have a value.';
COMMENT ON COLUMN nba.stat_coverage.stat IS
    'Column name in nba.player_game_stats, e.g. ''blk''.';
COMMENT ON COLUMN nba.stat_coverage.player_rows IS 'Player rows (box score lines) that season.';
COMMENT ON COLUMN nba.stat_coverage.recorded_rows IS 'Player rows where this stat is not NULL.';
COMMENT ON COLUMN nba.stat_coverage.pct_recorded IS
    'recorded_rows as a percentage of player_rows, rounded to one decimal place.';

CREATE VIEW nba.stat_tracked_since AS
WITH gaps AS (
    -- The latest season in which the stat was NOT fully recorded (under 98%).
    SELECT stat, max(season) FILTER (WHERE recorded_rows < 0.98 * player_rows) AS last_gap
    FROM nba.stat_coverage
    GROUP BY stat
)
SELECT
    c.stat,
    min(c.season) FILTER (WHERE c.recorded_rows > 0) AS first_recorded_season,
    min(c.season) FILTER (WHERE g.last_gap IS NULL OR c.season > g.last_gap) AS tracked_since
FROM nba.stat_coverage c
JOIN gaps g USING (stat)
GROUP BY c.stat;

COMMENT ON VIEW nba.stat_tracked_since IS
    'When each stat in nba.player_game_stats was recorded: one row per stat. Before '
    'first_recorded_season the stat was never recorded, so questions about it must be refused. '
    'From first_recorded_season until tracked_since it was only partly recorded, so answers '
    'must say the figures are incomplete. From tracked_since on it is fully recorded.';
COMMENT ON COLUMN nba.stat_tracked_since.stat IS
    'Column name in nba.player_game_stats, e.g. ''blk''.';
COMMENT ON COLUMN nba.stat_tracked_since.first_recorded_season IS
    'First season with any recorded value. NULL if never recorded.';
COMMENT ON COLUMN nba.stat_tracked_since.tracked_since IS
    'First season from which every season has the stat in at least 98% of player rows. '
    'NULL if the latest season falls short.';
