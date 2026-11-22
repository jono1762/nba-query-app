-- Team box scores: one row per team per game, from stats.nba.com's team-level game
-- log. This is the source's own record of each team's final score and totals, so
-- it stays right even where player rows are wrong or missing (e.g. a player the
-- source lost). Stats not recorded in early seasons are NULL, as for players.

CREATE TABLE nba.team_game_stats (
    game_id           text    NOT NULL REFERENCES nba.games,
    team_id           integer NOT NULL REFERENCES nba.teams,
    team_abbreviation text    NOT NULL,
    opponent_team_id  integer NOT NULL REFERENCES nba.teams,
    is_home           boolean,
    won               boolean NOT NULL,
    pts               integer NOT NULL CHECK (pts >= 0),
    opponent_pts      integer NOT NULL CHECK (opponent_pts >= 0),
    minutes           integer CHECK (minutes > 0),
    fgm               integer CHECK (fgm >= 0),
    fga               integer CHECK (fga >= fgm),
    fg3m              integer CHECK (fg3m >= 0),
    fg3a              integer CHECK (fg3a >= fg3m),
    ftm               integer CHECK (ftm >= 0),
    fta               integer CHECK (fta >= ftm),
    oreb              integer CHECK (oreb >= 0),
    dreb              integer CHECK (dreb >= 0),
    reb               integer CHECK (reb >= 0),
    ast               integer CHECK (ast >= 0),
    stl               integer CHECK (stl >= 0),
    blk               integer CHECK (blk >= 0),
    tov               integer CHECK (tov >= 0),
    pf                integer CHECK (pf >= 0),
    PRIMARY KEY (game_id, team_id),
    CHECK (team_id <> opponent_team_id),
    CHECK (won = (pts > opponent_pts))
);
CREATE INDEX team_game_stats_team_idx ON nba.team_game_stats (team_id);

COMMENT ON TABLE nba.team_game_stats IS
    'One row per team per game (team box score), from 1946-47: final score and team totals. '
    'Join to nba.games for date and season. Use this for scores, margins, team records and '
    'team totals; adding up nba.player_game_stats can differ slightly in some old games. '
    'A NULL stat means it was not recorded for that game, which is different from 0.';
COMMENT ON COLUMN nba.team_game_stats.team_abbreviation IS
    'The team''s abbreviation at the time of the game (historical, e.g. NJN before the Nets moved).';
COMMENT ON COLUMN nba.team_game_stats.opponent_team_id IS 'The team they played against.';
COMMENT ON COLUMN nba.team_game_stats.is_home IS
    'True if this team was the home team, false if away, NULL at a neutral site.';
COMMENT ON COLUMN nba.team_game_stats.won IS 'True if this team won the game.';
COMMENT ON COLUMN nba.team_game_stats.pts IS 'This team''s final score.';
COMMENT ON COLUMN nba.team_game_stats.opponent_pts IS 'The opponent''s final score.';
COMMENT ON COLUMN nba.team_game_stats.minutes IS
    'Total player minutes: 240 for a regulation game, plus 25 per overtime (5 players x 5 '
    'minutes), so (minutes - 240) / 25 is the number of overtimes. NULL if not recorded '
    '(early seasons).';
COMMENT ON COLUMN nba.team_game_stats.fgm IS
    'Field goals made, including 3-pointers. NULL if not recorded (a few very early games).';
COMMENT ON COLUMN nba.team_game_stats.fga IS
    'Field goals attempted, including 3-pointers. NULL if not recorded.';
COMMENT ON COLUMN nba.team_game_stats.fg3m IS
    '3-point field goals made. NULL if not recorded (no 3-point line before 1979-80).';
COMMENT ON COLUMN nba.team_game_stats.fg3a IS
    '3-point field goals attempted. NULL if not recorded (no 3-point line before 1979-80).';
COMMENT ON COLUMN nba.team_game_stats.ftm IS
    'Free throws made. NULL if not recorded (a few very early games).';
COMMENT ON COLUMN nba.team_game_stats.fta IS 'Free throws attempted. NULL if not recorded.';
COMMENT ON COLUMN nba.team_game_stats.oreb IS 'Offensive rebounds. NULL if not recorded.';
COMMENT ON COLUMN nba.team_game_stats.dreb IS 'Defensive rebounds. NULL if not recorded.';
COMMENT ON COLUMN nba.team_game_stats.reb IS 'Total rebounds. NULL if not recorded.';
COMMENT ON COLUMN nba.team_game_stats.ast IS 'Assists. NULL if not recorded.';
COMMENT ON COLUMN nba.team_game_stats.stl IS 'Steals. NULL if not recorded.';
COMMENT ON COLUMN nba.team_game_stats.blk IS 'Blocks. NULL if not recorded.';
COMMENT ON COLUMN nba.team_game_stats.tov IS 'Turnovers. NULL if not recorded.';
COMMENT ON COLUMN nba.team_game_stats.pf IS 'Personal fouls. NULL if not recorded.';
