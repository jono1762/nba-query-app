-- Playoff rounds and series. The round of each playoff game is inferred from who
-- played whom (see ingest/playoffs.py), because the source's own round labels are
-- unreliable before 2001-02.

ALTER TABLE nba.games
    ADD COLUMN playoff_round integer CHECK (playoff_round BETWEEN 1 AND 4),
    ADD COLUMN playoff_round_name text CHECK (playoff_round_name IN (
        'First Round', 'Division Semifinals', 'Division Finals',
        'Conference Semifinals', 'Conference Finals', 'Finals'
    )),
    ADD COLUMN playoff_series_game integer CHECK (playoff_series_game >= 1);

-- Only playoff games have a round, and the three columns are set together.
ALTER TABLE nba.games ADD CONSTRAINT games_playoff_round_only_in_playoffs CHECK (
    (playoff_round IS NULL AND playoff_round_name IS NULL AND playoff_series_game IS NULL)
    OR (season_type = 'Playoffs' AND playoff_round IS NOT NULL
        AND playoff_round_name IS NOT NULL AND playoff_series_game IS NOT NULL)
);

COMMENT ON COLUMN nba.games.playoff_round IS
    'Playoff round number: 1 = that season''s first round, counting up to the Finals. The '
    'number of rounds varies by season (4 since 1983-84, fewer before), so use '
    'playoff_round_name to find e.g. the Finals. NULL for non-playoff games, and for the '
    '1953-54 playoffs (a round-robin format whose rounds can''t be worked out).';
COMMENT ON COLUMN nba.games.playoff_round_name IS
    'Playoff round name: ''First Round'', ''Conference Semifinals'', ''Conference Finals'' or '
    '''Finals''. Before 1970-71 (divisions, not conferences): ''Division Semifinals'' and '
    '''Division Finals''. In seasons with fewer rounds the first round is named after its '
    'place before the Finals (e.g. ''Division Semifinals'' in the 1960s). NULL for '
    'non-playoff games and the 1953-54 playoffs.';
COMMENT ON COLUMN nba.games.playoff_series_game IS
    'Game number within the playoff series (1 = first game). NULL for non-playoff games and '
    'the 1953-54 playoffs.';

CREATE VIEW nba.playoff_series AS
WITH per_team AS (
    SELECT
        g.season,
        g.playoff_round,
        g.playoff_round_name,
        t.team_id,
        t.opponent_team_id,
        max(t.team_abbreviation) AS team_abbreviation,
        count(*) FILTER (WHERE t.won)::integer AS wins,
        count(*)::integer AS games,
        min(g.game_date) AS first_game_date,
        max(g.game_date) AS last_game_date
    FROM nba.games g
    JOIN nba.team_game_stats t USING (game_id)
    WHERE g.playoff_round IS NOT NULL
    GROUP BY g.season, g.playoff_round, g.playoff_round_name, t.team_id, t.opponent_team_id
)
SELECT
    w.season,
    w.playoff_round,
    w.playoff_round_name,
    w.team_id AS winner_team_id,
    w.team_abbreviation AS winner_abbreviation,
    l.team_id AS loser_team_id,
    l.team_abbreviation AS loser_abbreviation,
    w.wins AS winner_wins,
    l.wins AS loser_wins,
    w.games,
    w.first_game_date,
    w.last_game_date
FROM per_team w
JOIN per_team l
    ON l.season = w.season
    AND l.playoff_round = w.playoff_round
    AND l.team_id = w.opponent_team_id
    AND l.opponent_team_id = w.team_id
WHERE w.wins > l.wins OR (w.wins = l.wins AND w.team_id < l.team_id);

COMMENT ON VIEW nba.playoff_series IS
    'One row per playoff series, from 1946-47 (except 1953-54): the two teams, who won and '
    'the series score, e.g. 4-3. The champion of a season is the winner of its ''Finals'' '
    'series. A series in the current season may still be in progress: then "winner" is the '
    'team currently ahead (winner_wins = loser_wins if tied).';
COMMENT ON COLUMN nba.playoff_series.playoff_round IS
    'Round number: 1 = that season''s first round (see nba.games.playoff_round).';
COMMENT ON COLUMN nba.playoff_series.playoff_round_name IS
    'Round name, e.g. ''First Round'' or ''Finals'' (see nba.games.playoff_round_name).';
COMMENT ON COLUMN nba.playoff_series.winner_abbreviation IS
    'The winner''s abbreviation at the time (historical).';
COMMENT ON COLUMN nba.playoff_series.loser_abbreviation IS
    'The loser''s abbreviation at the time (historical).';
COMMENT ON COLUMN nba.playoff_series.winner_wins IS 'Games the winner won in the series.';
COMMENT ON COLUMN nba.playoff_series.loser_wins IS 'Games the loser won in the series.';
COMMENT ON COLUMN nba.playoff_series.games IS 'Games played in the series.';
