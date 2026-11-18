-- Teams and players get renamed (Seattle SuperSonics -> Oklahoma City Thunder,
-- Ron Artest -> Metta World Peace). Record the game date a name was last seen,
-- so loading an old season after a newer one never overwrites the newer name.

ALTER TABLE nba.teams ADD COLUMN name_as_of date NOT NULL;
COMMENT ON COLUMN nba.teams.name_as_of IS 'Date of the latest game the abbreviation/name was taken from.';

ALTER TABLE nba.players ADD COLUMN name_as_of date NOT NULL;
COMMENT ON COLUMN nba.players.name_as_of IS 'Date of the latest game the name was taken from.';
