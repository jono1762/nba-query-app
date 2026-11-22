-- The draft_year comment now says only the year is stored, so age at the draft is
-- approximate.

COMMENT ON COLUMN nba.player_details.draft_year IS
    'Year of the draft the player was picked in (held in June, after the season). NULL if '
    'undrafted (or not recorded, e.g. before the draft began in 1947). Only the year is '
    'stored, not the date, so age at the draft is approximate (it can be off by a year): '
    'say so when answering. Age in a game is exact: age(game_date, birth_date).';
