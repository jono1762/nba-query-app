"""The AI's instructions."""

import re
from datetime import date

import psycopg
from conftest import NbaData

from nba_query_app.query.prompt import (
    ANSWER_MAX_ROWS,
    EXAMPLES,
    QuestionContext,
    answer_request,
    describe_schema,
    sql_instructions,
    sql_request,
)
from nba_query_app.query.run import QueryResult
from nba_query_app.query.safety import ALLOWED_TABLES, check_sql

CONTEXT = QuestionContext(
    today=date(2026, 11, 23), current_season="2026-27", latest_game_date=date(2026, 11, 20)
)


def test_schema_lists_every_allowed_table_with_column_comments(nba_data: NbaData) -> None:
    schema = describe_schema(nba_data.conn)

    described = set(re.findall(r"^(?:TABLE|VIEW) nba\.(\w+):", schema, re.MULTILINE))
    assert described == ALLOWED_TABLES
    assert "  pts integer: Points." in schema
    assert "VIEW nba.playoff_series: One row per playoff series" in schema


def test_schema_includes_when_each_stat_was_recorded(nba_data: NbaData) -> None:
    nba_data.game("g1", "1980-81")
    nba_data.stats("g1", 1, 1, won=True, blk=None, fg3m=1, fg3a=2)

    schema = describe_schema(nba_data.conn)

    assert "  fg3m: first 1980-81, fully from 1980-81" in schema
    assert "  blk: first never, fully from -" in schema


def test_schema_leaves_no_transaction_open(nba_data: NbaData) -> None:
    describe_schema(nba_data.conn)
    assert nba_data.conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


def test_examples_pass_the_safety_checker() -> None:
    queries = [q.strip() for q in re.split(r"\nQuestion: .*\n(?:\(.*\)\n)?", EXAMPLES)[1:]]
    assert len(queries) == 3
    for sql in queries:
        check_sql(sql)


def test_instructions_include_the_schema() -> None:
    assert sql_instructions("SCHEMA HERE").endswith("SCHEMA HERE")


def test_request_has_the_date_season_and_latest_game() -> None:
    request = sql_request("How many points did Eddie Johnson score?", CONTEXT)

    assert request.splitlines() == [
        "Today is 2026-11-23. The current season is 2026-27.",
        "The newest game in the database was played on 2026-11-20.",
        "",
        "Question: How many points did Eddie Johnson score?",
    ]


def test_request_without_games() -> None:
    context = QuestionContext(date(2026, 11, 23), "2026-27", None)
    assert "played on unknown" in sql_request("Who won the most games?", context)


def test_names_are_matched_by_contains_never_by_id() -> None:
    # The AI copies the examples closely, so none may filter on a player_id number.
    assert "ILIKE '%Russell Westbrook%'" in EXAMPLES
    assert not re.search(r"player_id\s*(=|IN)\s*\(?\d", EXAMPLES)


def test_answer_request_shows_rows_and_nulls() -> None:
    result = QueryResult(["full_name", "pts"], [("Wilt Chamberlain", 100), ("Nobody", None)], False)

    request = answer_request("Most points in a game?", "SELECT ...", result)

    assert request.splitlines()[-3:] == [
        "full_name | pts",
        "Wilt Chamberlain | 100",
        "Nobody | NULL",
    ]


def test_answer_request_with_no_rows() -> None:
    request = answer_request("q", "SELECT ...", QueryResult(["x"], [], False))
    assert request.endswith("x\n(no rows)")


def test_answer_request_caps_rows_and_says_so() -> None:
    rows = [(i,) for i in range(ANSWER_MAX_ROWS + 5)]

    request = answer_request("q", "SELECT ...", QueryResult(["n"], rows, False))

    assert f"{ANSWER_MAX_ROWS - 1}\n(and more rows not shown: 5)" in request
    assert f"\n{ANSWER_MAX_ROWS}\n" not in request


def test_answer_request_says_when_the_query_had_more_rows() -> None:
    request = answer_request("q", "SELECT ...", QueryResult(["n"], [(1,)], True))
    assert request.endswith("(and more rows not shown)")


def test_answer_request_passes_on_the_query_note() -> None:
    result = QueryResult(["full_name", "pts"], [("LeBron James", 43628)], False)

    request = answer_request("Points by James?", "SELECT ...", result, note="Assuming LeBron James")

    assert "Query note: Assuming LeBron James" in request
    assert "Query note" not in answer_request("q", "SELECT ...", result)
