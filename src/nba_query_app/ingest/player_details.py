"""Player details: position, height, weight, college, country, draft and birth date.

Two sources on stats.nba.com:
- the player index: every player in history in one request, with everything
  except birth date;
- each player's info: one request per player, with the birth date.

So birth dates are fetched only for players not looked up yet
(`birth_date_checked`), one at a time, each saved as it arrives. The first run
fetches about 5,200 players (about 2 hours); after that, only players new to the
database. Rerunning after an interruption resumes where it stopped.

Usage:
    uv run --env-file .env python -m nba_query_app.ingest.player_details

The backfill also runs this at the end, so the daily update picks up new players.
"""

import os
import time
from collections.abc import Callable
from dataclasses import astuple, dataclass, fields
from datetime import date
from typing import Any

import psycopg
from psycopg import sql

from nba_query_app.ingest.fetch import fetch_player_index, fetch_player_info


@dataclass(frozen=True)
class PlayerDetails:
    """One row of nba.player_details, except birth date (set separately)."""

    player_id: int
    position: str | None
    height_inches: int | None
    weight_lbs: int | None
    college: str | None
    country: str | None
    draft_year: int | None
    draft_round: int | None
    draft_number: int | None


def _text(value: Any) -> str | None:
    """Blank and '-' mean "not listed"."""
    if value is None:
        return None
    text = str(value).strip()
    return None if text in ("", "-") else text


def parse_height(value: Any) -> int | None:
    """'6-10' -> 82 (inches)."""
    text = _text(value)
    if text is None:
        return None
    feet, inches = text.split("-")
    return int(feet) * 12 + int(inches)


def _int(value: Any) -> int | None:
    text = _text(value)
    return None if text is None else int(text)


def normalize_player_index(response: dict[str, Any]) -> dict[int, PlayerDetails]:
    """The player index response, as details keyed by player ID."""
    result = response["resultSets"][0]
    rows = [dict(zip(result["headers"], values, strict=True)) for values in result["rowSet"]]
    return {
        row["PERSON_ID"]: PlayerDetails(
            player_id=row["PERSON_ID"],
            position=_text(row["POSITION"]),
            height_inches=parse_height(row["HEIGHT"]),
            weight_lbs=_int(row["WEIGHT"]),
            college=_text(row["COLLEGE"]),
            country=_text(row["COUNTRY"]),
            draft_year=_int(row["DRAFT_YEAR"]),
            draft_round=_int(row["DRAFT_ROUND"]),
            draft_number=_int(row["DRAFT_NUMBER"]),
        )
        for row in rows
    }


def parse_birth_date(response: dict[str, Any] | None) -> date | None:
    """The birth date from one player's info ('1936-08-21T00:00:00'), or None."""
    if response is None:
        return None
    result = response["resultSets"][0]
    if not result["rowSet"]:
        return None
    birth = _text(dict(zip(result["headers"], result["rowSet"][0], strict=True))["BIRTHDATE"])
    return None if birth is None else date.fromisoformat(birth[:10])


# NBA.com fills in an unknown birth date for some early players with a day in early
# April 1905 (14 players, 1946-47 to 1954-55, e.g. Johnny Ezersky, really born in 1922).
# No real NBA player was born then, so these are treated as not recorded.
PLACEHOLDER_BIRTH_MONTH = (1905, 4)


def is_placeholder_birth_date(birth_date: date) -> bool:
    return (birth_date.year, birth_date.month) == PLACEHOLDER_BIRTH_MONTH


_COLUMNS = [f.name for f in fields(PlayerDetails)]
_UPDATED = [c for c in _COLUMNS if c != "player_id"]
UPSERT_DETAILS = sql.SQL(
    "INSERT INTO nba.player_details ({columns}) VALUES ({values}) "
    "ON CONFLICT (player_id) DO UPDATE SET {assignments} "
    "WHERE ({current}) IS DISTINCT FROM ({incoming})"
).format(
    columns=sql.SQL(", ").join(map(sql.Identifier, _COLUMNS)),
    values=sql.SQL(", ").join(sql.Placeholder() * len(_COLUMNS)),
    assignments=sql.SQL(", ").join(
        sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in _UPDATED
    ),
    current=sql.SQL(", ").join(
        sql.SQL("nba.player_details.{}").format(sql.Identifier(c)) for c in _UPDATED
    ),
    incoming=sql.SQL(", ").join(sql.SQL("EXCLUDED.{}").format(sql.Identifier(c)) for c in _UPDATED),
)

BUMP_VERSION = "UPDATE nba.meta SET data_version = data_version + 1, updated_at = now()"


def load_player_details(conn: psycopg.Connection, details: dict[int, PlayerDetails]) -> int:
    """Upsert a details row for every player in nba.players; returns rows changed.

    Players missing from the index get a row of NULLs, so every player has a row.
    Birth dates are left alone.
    """
    with conn.transaction(), conn.cursor() as cur:
        ours = [player_id for (player_id,) in cur.execute("SELECT player_id FROM nba.players")]
        empty = dict.fromkeys(_UPDATED)
        rows = [details.get(pid) or PlayerDetails(player_id=pid, **empty) for pid in ours]
        if not rows:
            return 0
        cur.executemany(UPSERT_DETAILS, [astuple(row) for row in rows])
        changed = cur.rowcount
        if changed:
            cur.execute(BUMP_VERSION)
    return changed


def players_needing_birth_dates(conn: psycopg.Connection) -> list[int]:
    # In its own transaction: a bare read on a connection that isn't in autocommit
    # mode would leave a transaction open, turning every later save into a savepoint
    # inside it, so nothing would be committed until the very end.
    with conn.transaction():
        rows = conn.execute(
            "SELECT player_id FROM nba.player_details WHERE NOT birth_date_checked "
            "ORDER BY player_id"
        ).fetchall()
    return [player_id for (player_id,) in rows]


def save_birth_date(conn: psycopg.Connection, player_id: int, birth_date: date | None) -> None:
    """Record a looked-up birth date (None if the source has none), in its own transaction."""
    with conn.transaction():
        conn.execute(
            "UPDATE nba.player_details SET birth_date = %s, birth_date_checked = true "
            "WHERE player_id = %s",
            (birth_date, player_id),
        )
        if birth_date is not None:
            conn.execute(BUMP_VERSION)


@dataclass(frozen=True)
class RefreshResult:
    details_changed: int
    birth_dates_checked: int
    birth_dates_found: int
    placeholder_birth_dates: int  # placeholders treated as not recorded (also not found)


def refresh_player_details(
    conn: psycopg.Connection,
    *,
    fetch_index: Callable[[], dict[str, Any]] = fetch_player_index,
    fetch_info: Callable[[int], dict[str, Any] | None] = fetch_player_info,
    pause_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> RefreshResult:
    """Load the player index, then look up birth dates for players not checked yet."""
    changed = load_player_details(conn, normalize_player_index(fetch_index()))
    todo = players_needing_birth_dates(conn)
    log(f"Player details: {changed} rows new/changed; {len(todo)} birth date(s) to look up.")

    found = placeholders = 0
    for index, player_id in enumerate(todo, start=1):
        sleep(pause_seconds)  # be polite to stats.nba.com
        birth_date = parse_birth_date(fetch_info(player_id))
        if birth_date is not None and is_placeholder_birth_date(birth_date):
            birth_date = None
            placeholders += 1
        save_birth_date(conn, player_id, birth_date)
        found += birth_date is not None
        if index % 100 == 0 or index == len(todo):
            log(
                f"  birth dates: {index}/{len(todo)} looked up, {found} found, "
                f"{placeholders} placeholder(s) treated as not recorded"
            )
    return RefreshResult(changed, len(todo), found, placeholders)


def main() -> None:
    started = time.perf_counter()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        result = refresh_player_details(conn)
    print(
        f"Done in {time.perf_counter() - started:.0f}s: {result.details_changed} detail rows "
        f"new/changed, {result.birth_dates_found} of {result.birth_dates_checked} "
        f"birth dates found ({result.placeholder_birth_dates} placeholder(s) treated as "
        "not recorded)."
    )


if __name__ == "__main__":
    main()
