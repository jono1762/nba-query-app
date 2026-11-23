-- The 1953-54 playoffs began with a round robin (three teams in each division each
-- played the other two), then division finals and the Finals. They now get rounds:
-- the round robin is round 1, 'Round Robin' (see ingest/playoffs.py).

ALTER TABLE nba.games DROP CONSTRAINT games_playoff_round_name_check;
ALTER TABLE nba.games ADD CONSTRAINT games_playoff_round_name_check CHECK (
    playoff_round_name IN (
        'Round Robin', 'First Round', 'Division Semifinals', 'Division Finals',
        'Conference Semifinals', 'Conference Finals', 'Finals'
    )
);

COMMENT ON COLUMN nba.games.playoff_round IS
    'Playoff round number: 1 = that season''s first round, counting up to the Finals. The '
    'number of rounds varies by season (4 since 1983-84, fewer before), so use '
    'playoff_round_name to find e.g. the Finals. NULL for non-playoff games.';
COMMENT ON COLUMN nba.games.playoff_round_name IS
    'Playoff round name: ''First Round'', ''Conference Semifinals'', ''Conference Finals'' or '
    '''Finals''. Before 1970-71 (divisions, not conferences): ''Division Semifinals'' and '
    '''Division Finals''. In seasons with fewer rounds the first round is named after its '
    'place before the Finals (e.g. ''Division Semifinals'' in the 1960s). The 1953-54 '
    'playoffs began with a ''Round Robin'' (each team in a division played the others; not '
    'a series). NULL for non-playoff games.';
COMMENT ON COLUMN nba.games.playoff_series_game IS
    'Game number within the playoff series (1 = first game). In the 1953-54 round robin, '
    'the game number between those two teams. NULL for non-playoff games.';

-- Same view, without the round robin, which wasn't a series.
CREATE OR REPLACE VIEW nba.playoff_series AS
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
    WHERE g.playoff_round IS NOT NULL AND g.playoff_round_name <> 'Round Robin'
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
    'One row per playoff series, from 1946-47: the two teams, who won and the series score, '
    'e.g. 4-3. The champion of a season is the winner of its ''Finals'' series. The 1953-54 '
    'round robin isn''t a series and isn''t listed (its division finals and Finals are). A '
    'series in the current season may still be in progress: then "winner" is the team '
    'currently ahead (winner_wins = loser_wins if tied).';
