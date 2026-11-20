-- A player can (very rarely) record stats for both teams in the same game.
-- Real example: the protested 76ers-Nets game of 1978-11-08 was finished months
-- later, after Eric Money had been traded from the Nets to the 76ers, so he has
-- genuine stats for both teams in that one game. The row key therefore includes
-- the team.

ALTER TABLE nba.player_game_stats DROP CONSTRAINT player_game_stats_pkey;
ALTER TABLE nba.player_game_stats ADD PRIMARY KEY (game_id, player_id, team_id);

COMMENT ON TABLE nba.player_game_stats IS
    'One row per player per team per game they played in (box score), from 1946-47. Join to '
    'nba.games for date and season. A NULL stat means it was not recorded for that game '
    '(common in early seasons), which is different from 0. A player has two rows in one game '
    'only in the rare case they played for both teams (e.g. a protested game finished after a trade).';
