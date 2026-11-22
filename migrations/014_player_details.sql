-- Player details from stats.nba.com: position, height, weight, birth date, college,
-- country and draft. One row per player in nba.players. Everything except birth
-- date comes from the player index (one request); birth dates come from each
-- player's info (one request per player), so they are set separately and
-- birth_date_checked records which players have been looked up.

CREATE TABLE nba.player_details (
    player_id          integer PRIMARY KEY REFERENCES nba.players,
    position           text CHECK (position IN ('G', 'F', 'C', 'G-F', 'F-G', 'F-C', 'C-F')),
    height_inches      integer CHECK (height_inches BETWEEN 60 AND 96),
    weight_lbs         integer CHECK (weight_lbs BETWEEN 100 AND 400),
    birth_date         date,
    college            text,
    country            text,
    draft_year         integer,
    draft_round        integer CHECK (draft_round >= 1),
    draft_number       integer CHECK (draft_number >= 1),
    birth_date_checked boolean NOT NULL DEFAULT false
);

COMMENT ON TABLE nba.player_details IS
    'Biographical details, one row per player in nba.players (join on player_id). Each value '
    'is a single figure for the whole career as NBA.com lists it today, not per season: '
    'position and weight can be from late in a career. NULL means not listed.';
COMMENT ON COLUMN nba.player_details.position IS
    'Position as NBA.com lists it, for the whole career: ''G'' (guard), ''F'' (forward), '
    '''C'' (center), or two letters for both, main one first (e.g. ''G-F'' = guard-forward, '
    '''F-C'' = forward-center). Not per season, so it can be misleading for players who '
    'changed roles. NULL for a few early players.';
COMMENT ON COLUMN nba.player_details.height_inches IS
    'Height in inches (e.g. 82 = 6 ft 10 in). NULL for a few early players.';
COMMENT ON COLUMN nba.player_details.weight_lbs IS
    'Weight in pounds, as last listed. NULL for a few early players.';
COMMENT ON COLUMN nba.player_details.birth_date IS
    'Date of birth. A player''s age on a game day is age(game_date, birth_date). NULL if '
    'NBA.com has no record.';
COMMENT ON COLUMN nba.player_details.college IS
    'Last school before the NBA: usually a college, sometimes a high school or a club abroad. '
    'NULL if not listed.';
COMMENT ON COLUMN nba.player_details.country IS 'Country, as NBA.com lists it (e.g. USA, France).';
COMMENT ON COLUMN nba.player_details.draft_year IS
    'Year of the draft the player was picked in. NULL if undrafted (or not recorded, e.g. '
    'before the draft began in 1947).';
COMMENT ON COLUMN nba.player_details.draft_round IS
    'Draft round (1 = first round). NULL if undrafted, or for picks with no round (e.g. '
    'early territorial picks).';
COMMENT ON COLUMN nba.player_details.draft_number IS
    'Overall pick number (1 = first pick). NULL if undrafted or not recorded.';
COMMENT ON COLUMN nba.player_details.birth_date_checked IS
    'Bookkeeping for the loader: true once the birth date has been looked up. Not a stat.';
