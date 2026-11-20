-- The NBA Cup (in-season tournament, from 2023-24) championship game is a
-- competitive game but doesn't count in regular-season stats, so it gets its own
-- season type. (The other Cup games do count as regular season and are stored as such.)

ALTER TABLE nba.games DROP CONSTRAINT games_season_type_check;
ALTER TABLE nba.games ADD CONSTRAINT games_season_type_check
    CHECK (season_type IN ('Regular Season', 'NBA Cup Final', 'Play-In', 'Playoffs'));
COMMENT ON COLUMN nba.games.season_type IS
    'One of ''Regular Season'', ''NBA Cup Final'' (in-season tournament championship game, '
    'from 2023-24; not counted in regular-season stats), ''Play-In'' (play-in tournament, '
    'from 2019-20) or ''Playoffs''. Exhibitions such as the All-Star game are not included.';
