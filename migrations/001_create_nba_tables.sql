-- NBA stats tables. Source: stats.nba.com LeagueGameLog (one row per player per game).
-- Column comments are written for humans AND will be fed to the LLM as schema docs.

CREATE SCHEMA nba;
COMMENT ON SCHEMA nba IS 'NBA player game statistics from stats.nba.com.';

CREATE TABLE nba.teams (
    team_id      integer PRIMARY KEY,
    abbreviation text NOT NULL,
    name         text NOT NULL
);
COMMENT ON TABLE nba.teams IS 'NBA franchises, with their most recent abbreviation and name.';
COMMENT ON COLUMN nba.teams.team_id IS 'NBA.com team ID. Stays the same when a franchise moves or is renamed.';
COMMENT ON COLUMN nba.teams.abbreviation IS 'Most recent abbreviation, e.g. LAL, BOS, OKC.';
COMMENT ON COLUMN nba.teams.name IS 'Most recent full name, e.g. Los Angeles Lakers.';

CREATE TABLE nba.players (
    player_id integer PRIMARY KEY,
    full_name text NOT NULL
);
COMMENT ON TABLE nba.players IS 'Every player who has appeared in a game in the dataset.';
COMMENT ON COLUMN nba.players.full_name IS 'Full name as listed by NBA.com, e.g. LeBron James.';

CREATE TABLE nba.games (
    game_id      text PRIMARY KEY,
    game_date    date NOT NULL,
    season       text NOT NULL CHECK (season ~ '^\d{4}-\d{2}$'),
    season_type  text NOT NULL CHECK (season_type IN ('Regular Season', 'Playoffs')),
    home_team_id integer NOT NULL REFERENCES nba.teams,
    away_team_id integer NOT NULL REFERENCES nba.teams,
    CHECK (home_team_id <> away_team_id)
);
CREATE INDEX games_season_idx ON nba.games (season, season_type);
CREATE INDEX games_date_idx ON nba.games (game_date);
COMMENT ON TABLE nba.games IS 'One row per game.';
COMMENT ON COLUMN nba.games.game_id IS 'NBA.com game ID, text with leading zeros, e.g. 0022600003.';
COMMENT ON COLUMN nba.games.game_date IS 'Local date the game was played.';
COMMENT ON COLUMN nba.games.season IS 'Season label, e.g. 2026-27 (season starting in October 2026).';
COMMENT ON COLUMN nba.games.season_type IS 'Either ''Regular Season'' or ''Playoffs''.';

CREATE TABLE nba.player_game_stats (
    game_id           text    NOT NULL REFERENCES nba.games,
    player_id         integer NOT NULL REFERENCES nba.players,
    team_id           integer NOT NULL REFERENCES nba.teams,
    team_abbreviation text    NOT NULL,
    opponent_team_id  integer NOT NULL REFERENCES nba.teams,
    is_home           boolean NOT NULL,
    won               boolean NOT NULL,
    minutes           integer NOT NULL CHECK (minutes >= 0),
    pts               integer NOT NULL CHECK (pts >= 0),
    fgm               integer NOT NULL CHECK (fgm >= 0),
    fga               integer NOT NULL CHECK (fga >= fgm),
    fg3m              integer NOT NULL CHECK (fg3m >= 0),
    fg3a              integer NOT NULL CHECK (fg3a >= fg3m),
    ftm               integer NOT NULL CHECK (ftm >= 0),
    fta               integer NOT NULL CHECK (fta >= ftm),
    oreb              integer NOT NULL CHECK (oreb >= 0),
    dreb              integer NOT NULL CHECK (dreb >= 0),
    reb               integer NOT NULL CHECK (reb >= 0),
    ast               integer NOT NULL CHECK (ast >= 0),
    stl               integer NOT NULL CHECK (stl >= 0),
    blk               integer NOT NULL CHECK (blk >= 0),
    tov               integer NOT NULL CHECK (tov >= 0),
    pf                integer NOT NULL CHECK (pf >= 0),
    plus_minus        integer,
    PRIMARY KEY (game_id, player_id)
);
CREATE INDEX player_game_stats_player_idx ON nba.player_game_stats (player_id);
COMMENT ON TABLE nba.player_game_stats IS
    'One row per player per game they played in (box score). Join to nba.games for date and season.';
COMMENT ON COLUMN nba.player_game_stats.team_abbreviation IS
    'The player''s team abbreviation at the time of the game (historical, e.g. NJN before the Nets moved).';
COMMENT ON COLUMN nba.player_game_stats.opponent_team_id IS 'The team they played against.';
COMMENT ON COLUMN nba.player_game_stats.is_home IS 'True if the player''s team was the home team.';
COMMENT ON COLUMN nba.player_game_stats.won IS 'True if the player''s team won the game.';
COMMENT ON COLUMN nba.player_game_stats.minutes IS 'Minutes played, rounded to a whole number.';
COMMENT ON COLUMN nba.player_game_stats.pts IS 'Points.';
COMMENT ON COLUMN nba.player_game_stats.fgm IS 'Field goals made (includes 3-pointers).';
COMMENT ON COLUMN nba.player_game_stats.fga IS 'Field goals attempted (includes 3-pointers).';
COMMENT ON COLUMN nba.player_game_stats.fg3m IS '3-point field goals made.';
COMMENT ON COLUMN nba.player_game_stats.fg3a IS '3-point field goals attempted.';
COMMENT ON COLUMN nba.player_game_stats.ftm IS 'Free throws made.';
COMMENT ON COLUMN nba.player_game_stats.fta IS 'Free throws attempted.';
COMMENT ON COLUMN nba.player_game_stats.oreb IS 'Offensive rebounds.';
COMMENT ON COLUMN nba.player_game_stats.dreb IS 'Defensive rebounds.';
COMMENT ON COLUMN nba.player_game_stats.reb IS 'Total rebounds.';
COMMENT ON COLUMN nba.player_game_stats.ast IS 'Assists.';
COMMENT ON COLUMN nba.player_game_stats.stl IS 'Steals.';
COMMENT ON COLUMN nba.player_game_stats.blk IS 'Blocks.';
COMMENT ON COLUMN nba.player_game_stats.tov IS 'Turnovers.';
COMMENT ON COLUMN nba.player_game_stats.pf IS 'Personal fouls.';
COMMENT ON COLUMN nba.player_game_stats.plus_minus IS
    'Point differential while on court. Can be NULL for older seasons.';

-- Single-row table describing the loaded data, used for "data as of" and cache keys.
CREATE TABLE nba.meta (
    id               boolean PRIMARY KEY DEFAULT true CHECK (id),
    data_version     bigint      NOT NULL DEFAULT 0,
    latest_game_date date,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
INSERT INTO nba.meta DEFAULT VALUES;
COMMENT ON TABLE nba.meta IS
    'Single row: data_version increases on every data load; latest_game_date is the newest game loaded.';
