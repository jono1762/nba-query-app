-- Season totals per player, calculated from the game rows so the two always agree.
--
-- One row per player per season per season type, with every team the player
-- played for combined (like a "TOT" line). A total is NULL unless the stat was
-- recorded in every one of the player's games that season: adding up only the
-- recorded games would silently undercount, e.g. blocks in 1979-80, when about a
-- fifth of box scores lack them. Use nba.player_game_stats for partial sums or
-- per-team splits.

CREATE VIEW nba.player_season_stats AS
SELECT
    s.player_id,
    p.full_name,
    g.season,
    g.season_type,
    count(DISTINCT s.game_id)::integer AS games,
    string_agg(DISTINCT s.team_abbreviation, ', ') AS team_abbreviations,
    CASE WHEN count(s.minutes) = count(*) THEN sum(s.minutes)::integer END AS minutes,
    sum(s.pts)::integer AS pts,
    sum(s.fgm)::integer AS fgm,
    CASE WHEN count(s.fga) = count(*) THEN sum(s.fga)::integer END AS fga,
    CASE WHEN count(s.fg3m) = count(*) THEN sum(s.fg3m)::integer END AS fg3m,
    CASE WHEN count(s.fg3a) = count(*) THEN sum(s.fg3a)::integer END AS fg3a,
    sum(s.ftm)::integer AS ftm,
    CASE WHEN count(s.fta) = count(*) THEN sum(s.fta)::integer END AS fta,
    CASE WHEN count(s.oreb) = count(*) THEN sum(s.oreb)::integer END AS oreb,
    CASE WHEN count(s.dreb) = count(*) THEN sum(s.dreb)::integer END AS dreb,
    CASE WHEN count(s.reb) = count(*) THEN sum(s.reb)::integer END AS reb,
    CASE WHEN count(s.ast) = count(*) THEN sum(s.ast)::integer END AS ast,
    CASE WHEN count(s.stl) = count(*) THEN sum(s.stl)::integer END AS stl,
    CASE WHEN count(s.blk) = count(*) THEN sum(s.blk)::integer END AS blk,
    CASE WHEN count(s.tov) = count(*) THEN sum(s.tov)::integer END AS tov,
    CASE WHEN count(s.pf) = count(*) THEN sum(s.pf)::integer END AS pf,
    CASE WHEN count(s.plus_minus) = count(*) THEN sum(s.plus_minus)::integer END AS plus_minus
FROM nba.player_game_stats s
JOIN nba.games g USING (game_id)
JOIN nba.players p USING (player_id)
GROUP BY s.player_id, p.full_name, g.season, g.season_type;

COMMENT ON VIEW nba.player_season_stats IS
    'Season totals: one row per player per season per season type, all teams combined, '
    'calculated from nba.player_game_stats. A stat total is NULL unless that stat was recorded '
    'in every one of the player''s games that season (so it never undercounts); use '
    'nba.player_game_stats for per-team splits or for sums over partially recorded seasons. '
    'Averages are totals divided by games (or by the games where the stat was recorded).';
COMMENT ON COLUMN nba.player_season_stats.full_name IS 'Player''s full name, as in nba.players.';
COMMENT ON COLUMN nba.player_season_stats.season IS 'Season label, e.g. 2026-27.';
COMMENT ON COLUMN nba.player_season_stats.season_type IS
    'Same values as nba.games.season_type: ''Regular Season'', ''NBA Cup Final'', ''Play-In'' or '
    '''Playoffs''. Filter on it: regular-season and playoff totals are separate rows.';
COMMENT ON COLUMN nba.player_season_stats.games IS 'Games played.';
COMMENT ON COLUMN nba.player_season_stats.team_abbreviations IS
    'Every team the player appeared for that season and season type, alphabetical and '
    'comma-separated (e.g. ''HOU, LAL'' after a trade).';
COMMENT ON COLUMN nba.player_season_stats.minutes IS
    'Total minutes. NULL unless recorded in every game.';
COMMENT ON COLUMN nba.player_season_stats.pts IS 'Total points (always recorded).';
COMMENT ON COLUMN nba.player_season_stats.fgm IS
    'Total field goals made, including 3-pointers (always recorded).';
COMMENT ON COLUMN nba.player_season_stats.fga IS
    'Total field goals attempted, including 3-pointers. NULL unless recorded in every game.';
COMMENT ON COLUMN nba.player_season_stats.fg3m IS
    'Total 3-pointers made. NULL unless recorded in every game (always NULL before 1979-80).';
COMMENT ON COLUMN nba.player_season_stats.fg3a IS
    'Total 3-pointers attempted. NULL unless recorded in every game (always NULL before 1979-80).';
COMMENT ON COLUMN nba.player_season_stats.ftm IS 'Total free throws made (always recorded).';
COMMENT ON COLUMN nba.player_season_stats.fta IS
    'Total free throws attempted. NULL unless recorded in every game.';
COMMENT ON COLUMN nba.player_season_stats.oreb IS
    'Total offensive rebounds. NULL unless recorded in every game.';
COMMENT ON COLUMN nba.player_season_stats.dreb IS
    'Total defensive rebounds. NULL unless recorded in every game.';
COMMENT ON COLUMN nba.player_season_stats.reb IS
    'Total rebounds. NULL unless recorded in every game.';
COMMENT ON COLUMN nba.player_season_stats.ast IS
    'Total assists. NULL unless recorded in every game.';
COMMENT ON COLUMN nba.player_season_stats.stl IS
    'Total steals. NULL unless recorded in every game.';
COMMENT ON COLUMN nba.player_season_stats.blk IS
    'Total blocks. NULL unless recorded in every game.';
COMMENT ON COLUMN nba.player_season_stats.tov IS
    'Total turnovers. NULL unless recorded in every game.';
COMMENT ON COLUMN nba.player_season_stats.pf IS
    'Total personal fouls. NULL unless recorded in every game.';
COMMENT ON COLUMN nba.player_season_stats.plus_minus IS
    'Total plus-minus. NULL unless recorded in every game.';
