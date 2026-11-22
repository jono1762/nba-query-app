"""Player details: converting the player index, birth dates, loading and refreshing."""

import json
from datetime import date
from pathlib import Path

import psycopg
import pytest
from conftest import NbaData

from nba_query_app.ingest.player_details import (
    PlayerDetails,
    load_player_details,
    normalize_player_index,
    parse_birth_date,
    parse_height,
    refresh_player_details,
)

FIXTURE = Path(__file__).parent / "fixtures" / "playerindex_sample.json"
WILT, CURRY, ABDELNABY, FONTAINE, LOPEZ = 76375, 201939, 76001, 1829, 1643510


@pytest.fixture
def index() -> dict:
    """Six real player-index rows, including blanks and '-' for "not listed"."""
    return json.loads(FIXTURE.read_text())


def info(birthdate: str | None) -> dict:
    """A one-player info response with just the field we read."""
    return {"resultSets": [{"headers": ["PERSON_ID", "BIRTHDATE"], "rowSet": [[1, birthdate]]}]}


# --- Converting ---------------------------------------------------------------------


def test_index_rows_become_details(index: dict) -> None:
    details = normalize_player_index(index)

    assert details[CURRY] == PlayerDetails(
        player_id=CURRY,
        position="G",
        height_inches=74,
        weight_lbs=205,
        college="Davidson",
        country="USA",
        draft_year=2009,
        draft_round=1,
        draft_number=7,
    )


def test_blanks_and_dashes_are_not_listed(index: dict) -> None:
    details = normalize_player_index(index)

    assert details[FONTAINE].weight_lbs is None  # ' ' in the source
    assert details[FONTAINE].position is None
    assert details[LOPEZ].college is None  # '-' in the source


def test_a_draft_pick_without_a_round_is_kept(index: dict) -> None:
    # Wilt Chamberlain: a 1959 territorial pick, listed with a year but no round or number.
    wilt = normalize_player_index(index)[WILT]
    assert (wilt.draft_year, wilt.draft_round, wilt.draft_number) == (1959, None, None)


@pytest.mark.parametrize(("text", "inches"), [("6-10", 82), ("7-1", 85), ("5-3", 63), (None, None)])
def test_parse_height(text: str | None, inches: int | None) -> None:
    assert parse_height(text) == inches


def test_parse_birth_date() -> None:
    assert parse_birth_date(info("1936-08-21T00:00:00")) == date(1936, 8, 21)
    assert parse_birth_date(info(None)) is None
    assert parse_birth_date(None) is None  # the source has no record of the player


# --- Loading --------------------------------------------------------------------------


@pytest.fixture
def players(nba_data: NbaData) -> psycopg.Connection:
    """Wilt and Curry in nba.players, plus one player the index doesn't have."""
    nba_data.player(WILT, "Wilt Chamberlain")
    nba_data.player(CURRY, "Stephen Curry")
    nba_data.player(79635, "Bobby Watson")  # real: missing from the player index
    return nba_data.conn


def details_row(conn: psycopg.Connection, player_id: int) -> tuple:
    return conn.execute(
        "SELECT height_inches, college, birth_date, birth_date_checked "
        "FROM nba.player_details WHERE player_id = %s",
        (player_id,),
    ).fetchone()


def version(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT data_version FROM nba.meta").fetchone()[0]


def test_every_player_gets_a_row(players: psycopg.Connection, index: dict) -> None:
    changed = load_player_details(players, normalize_player_index(index))

    assert changed == 3  # only players in nba.players; the other index rows are skipped
    assert details_row(players, CURRY) == (74, "Davidson", None, False)
    assert details_row(players, 79635) == (None, None, None, False)  # not in the index


def test_reloading_changes_nothing_and_keeps_birth_dates(
    players: psycopg.Connection, index: dict
) -> None:
    load_player_details(players, normalize_player_index(index))
    refresh_player_details(
        players,
        fetch_index=lambda: index,
        fetch_info=lambda player_id: info("1988-03-14T00:00:00"),
        sleep=lambda s: None,
        log=lambda line: None,
    )
    before = version(players)

    assert load_player_details(players, normalize_player_index(index)) == 0
    assert version(players) == before
    assert details_row(players, CURRY)[2:] == (date(1988, 3, 14), True)


# --- Refreshing -----------------------------------------------------------------------


def test_refresh_looks_up_each_birth_date_once(players: psycopg.Connection, index: dict) -> None:
    births = {WILT: "1936-08-21T00:00:00", CURRY: "1988-03-14T00:00:00"}
    asked: list[int] = []

    def fetch_info(player_id: int) -> dict | None:
        asked.append(player_id)
        return info(births[player_id]) if player_id in births else None

    def refresh() -> object:
        return refresh_player_details(
            players,
            fetch_index=lambda: index,
            fetch_info=fetch_info,
            sleep=lambda s: None,
            log=lambda line: None,
        )

    first = refresh()
    assert sorted(asked) == [WILT, 79635, CURRY]
    assert (first.birth_dates_checked, first.birth_dates_found) == (3, 2)
    assert details_row(players, WILT)[2:] == (date(1936, 8, 21), True)
    assert details_row(players, 79635)[2:] == (None, True)  # looked up, no record

    asked.clear()
    second = refresh()
    assert asked == []  # nobody is looked up twice, so a rerun resumes where it stopped
    assert second.birth_dates_checked == 0


def test_each_birth_date_is_committed_as_it_arrives(
    players: psycopg.Connection, index: dict, test_db_url: str
) -> None:
    # A normal connection (not autocommit, unlike the test fixtures), as the CLI uses.
    def fetch_info(player_id: int) -> dict | None:
        if player_id == 79635:  # the second player looked up
            raise RuntimeError("network down")
        return info("1936-08-21T00:00:00")

    # The connection is left by the error, as in a real crash: uncommitted work is lost.
    with pytest.raises(RuntimeError), psycopg.connect(test_db_url) as conn:
        refresh_player_details(
            conn,
            fetch_index=lambda: index,
            fetch_info=fetch_info,
            sleep=lambda s: None,
            log=lambda line: None,
        )

    # Seen from another connection: the first lookup was saved before the failure,
    # so a rerun resumes from the second.
    assert details_row(players, WILT)[2:] == (date(1936, 8, 21), True)
    assert details_row(players, 79635)[3] is False


def test_placeholder_birth_dates_are_not_recorded(players: psycopg.Connection, index: dict) -> None:
    # NBA.com lists some early players as born in early April 1905 when it doesn't know.
    births = {WILT: "1905-04-14T00:00:00", CURRY: "1988-03-14T00:00:00"}

    result = refresh_player_details(
        players,
        fetch_index=lambda: index,
        fetch_info=lambda player_id: info(births.get(player_id)),
        sleep=lambda s: None,
        log=lambda line: None,
    )

    assert details_row(players, WILT)[2:] == (None, True)  # looked up; treated as no record
    assert (result.birth_dates_found, result.placeholder_birth_dates) == (1, 1)
