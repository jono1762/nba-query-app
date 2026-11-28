-- Player names stored without accents ("Luka Doncic", not "Luka Dončić"): nobody
-- types accents in a question, and the source isn't consistent anyway ("Goran
-- Dragic"). New names are stripped by the loader (nba_query_app/names.py); this
-- strips the ones already stored, letter for letter the same (a test checks), and
-- a CHECK keeps full_name to plain ASCII from now on, so a letter missing here or
-- in names.py stops the load instead of slipping through.

WITH changed AS (
    UPDATE nba.players
    SET full_name = translate(
        replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(full_name, 'Æ', 'AE'), 'Þ', 'Th'), 'ß', 'ss'), 'æ', 'ae'), 'þ', 'th'), 'Ĳ', 'IJ'), 'ĳ', 'ij'), 'ŉ', '''n'), 'Œ', 'OE'), 'œ', 'oe'),
        'ÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝàáâãäåçèéêëìíîïðñòóôõöøùúûüýÿĀāĂăĄąĆćĈĉĊċČčĎďĐđĒēĔĕĖėĘęĚěĜĝĞğĠġĢģĤĥĦħĨĩĪīĬĭĮįİıĴĵĶķĸĹĺĻļĽľĿŀŁłŃńŅņŇňŊŋŌōŎŏŐőŔŕŖŗŘřŚśŜŝŞşŠšŢţŤťŦŧŨũŪūŬŭŮůŰűŲųŴŵŶŷŸŹźŻżŽžſ‘’',
        'AAAAAACEEEEIIIIDNOOOOOOUUUUYaaaaaaceeeeiiiidnoooooouuuuyyAaAaAaCcCcCcCcDdDdEeEeEeEeEeGgGgGgGgHhHhIiIiIiIiIiJjKkkLlLlLlLlLlNnNnNnNnOoOoOoRrRrRrSsSsSsSsTtTtTtUuUuUuUuUuUuWwYyYZzZzZzs''''')
    WHERE full_name ~ '[^ -~]'
    RETURNING 1
)
UPDATE nba.meta
SET data_version = data_version + 1, updated_at = now()
WHERE EXISTS (SELECT 1 FROM changed);

ALTER TABLE nba.players
    ADD CONSTRAINT players_full_name_plain CHECK (full_name ~ '^[ -~]+$');

COMMENT ON COLUMN nba.players.full_name IS
    'Full name as NBA.com lists it, but without accents (Luka Doncic, Nikola Jokic).';
