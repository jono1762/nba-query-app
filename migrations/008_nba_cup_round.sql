-- Mark NBA Cup (in-season tournament, from 2023-24) games with their round.
-- Group, quarterfinal and semifinal games count as regular-season games and keep
-- season_type 'Regular Season'; only the final has its own season type.
-- Set separately from the main game load (see ingest/cup.py), so it is NULL for
-- non-Cup games.

ALTER TABLE nba.games ADD COLUMN nba_cup_round text
    CHECK (nba_cup_round IN ('Group', 'Quarterfinal', 'Semifinal', 'Final'));

-- The 'Final' round and the 'NBA Cup Final' season type always go together.
-- (While the round is still NULL, e.g. just after the final is first loaded, this passes.)
ALTER TABLE nba.games ADD CONSTRAINT games_cup_final_round_matches_season_type
    CHECK ((nba_cup_round = 'Final') = (season_type = 'NBA Cup Final'));

COMMENT ON COLUMN nba.games.nba_cup_round IS
    'NBA Cup (in-season tournament, from 2023-24) round: ''Group'', ''Quarterfinal'', '
    '''Semifinal'' or ''Final''. NULL for games that are not NBA Cup games. Group, '
    'quarterfinal and semifinal games are also regular-season games (season_type '
    '''Regular Season''); the final is not (season_type ''NBA Cup Final'').';
