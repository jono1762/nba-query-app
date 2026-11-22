-- The birth_date comment now says how many players lack one, and why: NBA.com's
-- placeholder dates (early April 1905) are stored as NULL.

COMMENT ON COLUMN nba.player_details.birth_date IS
    'Date of birth. A player''s age on a game day is age(game_date, birth_date). NULL if '
    'not recorded: about 16 players, all but two from 1946-47 to 1954-55 (NBA.com lists '
    'placeholder dates for them, which are not kept).';
