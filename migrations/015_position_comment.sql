-- NBA.com only distinguishes guards, forwards and centers, so say so where the AI
-- reads it: questions about e.g. point guards must be answered with guards, with a note.

COMMENT ON COLUMN nba.player_details.position IS
    'Position as NBA.com lists it, for the whole career: ''G'' (guard), ''F'' (forward), '
    '''C'' (center), or two letters for both, main one first (e.g. ''G-F'' = guard-forward, '
    '''F-C'' = forward-center). Only guard, forward and center: there are no point guard, '
    'shooting guard, small forward or power forward labels, so answer such questions with '
    'guards or forwards and say so. Not per season, so it can be misleading for players who '
    'changed roles. NULL for a few early players.';
