-- Some games are played at a neutral site (e.g. international games). stats.nba.com
-- lists both teams as away ('IND @ DEN' and 'DEN @ IND'), so neither is at home.
-- For those games, home/away team is NULL and player rows have is_home = NULL.

ALTER TABLE nba.games ADD COLUMN neutral_site boolean NOT NULL DEFAULT false;
ALTER TABLE nba.games ALTER COLUMN home_team_id DROP NOT NULL;
ALTER TABLE nba.games ALTER COLUMN away_team_id DROP NOT NULL;
ALTER TABLE nba.games ADD CONSTRAINT games_neutral_site_has_no_home_team CHECK (
    (neutral_site AND home_team_id IS NULL AND away_team_id IS NULL)
    OR (NOT neutral_site AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL)
);
COMMENT ON COLUMN nba.games.neutral_site IS
    'True for games at a neutral venue; then home_team_id and away_team_id are NULL.';
COMMENT ON COLUMN nba.games.home_team_id IS 'Home team. NULL for neutral-site games.';
COMMENT ON COLUMN nba.games.away_team_id IS 'Away team. NULL for neutral-site games.';

ALTER TABLE nba.player_game_stats ALTER COLUMN is_home DROP NOT NULL;
COMMENT ON COLUMN nba.player_game_stats.is_home IS
    'True if the player''s team was the home team, false if away, NULL at a neutral site.';
