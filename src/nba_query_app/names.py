"""Player names are stored without accents: "Luka Doncic", not "Luka Dončić".

Nobody types accents in a question, and the source isn't consistent anyway (it has
"Luka Dončić" but "Goran Dragic"), so a name written the way people type it should
match the stored one. The loader (ingest/normalize.py) and the name lookup
(query/players.py) both use `strip_accents`, so they always agree.
Migration 019 did the same to the names already stored, and a CHECK constraint
keeps nba.players.full_name to plain ASCII characters.
"""

import unicodedata

# Letters that don't split into a plain letter plus an accent mark, and the curly
# apostrophe some sources use ("O’Neal").
SPECIAL_LETTERS = {
    "Ø": "O", "ø": "o", "Đ": "D", "đ": "d", "Ð": "D", "ð": "d", "Ł": "L", "ł": "l",
    "Ħ": "H", "ħ": "h", "ı": "i", "ß": "ss", "Æ": "AE", "æ": "ae", "Œ": "OE", "œ": "oe",
    "Þ": "Th", "þ": "th", "Ŋ": "N", "ŋ": "n", "Ŧ": "T", "ŧ": "t", "ĸ": "k", "Ŀ": "L",
    "ŀ": "l", "ŉ": "'n", "’": "'", "‘": "'",
}  # fmt: skip


def strip_accents(text: str) -> str:
    """'Nikola Jokić' -> 'Nikola Jokic', 'Kristaps Porziņģis' -> 'Kristaps Porzingis'.
    Case, spaces and punctuation are kept."""
    text = "".join(SPECIAL_LETTERS.get(c, c) for c in text)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))
