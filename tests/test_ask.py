"""Answering a question end to end, with a fake AI and a real read-only connection."""

import json
from collections.abc import Callable
from datetime import date

import psycopg
import pytest
from conftest import NbaData

from nba_query_app.llm.client import LlmQuotaError, LlmReply, Usage
from nba_query_app.query.ask import MAX_QUESTION_LENGTH, TwoStepAnswerer
from nba_query_app.query.prompt import ANSWER_INSTRUCTIONS, SQL_REPLY_SCHEMA

ConnectAs = Callable[[str], psycopg.Connection]
TOP_SCORER = (
    "SELECT p.full_name, s.pts FROM player_game_stats s JOIN players p USING (player_id) "
    "ORDER BY s.pts DESC LIMIT 1"
)


class FakeLlm:
    """Replies with the given texts (or raises the given errors) in order, recording calls."""

    def __init__(self, *outcomes: str | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def generate(self, *, system: str, prompt: str, json_schema: dict | None = None) -> LlmReply:
        self.calls.append({"system": system, "prompt": prompt, "json_schema": json_schema})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return LlmReply(outcome, "fake-model", Usage(100, 10, 1), 0.01)


def sql_reply(sql: str, answerable: bool = True, reason: str = "From the box scores.") -> str:
    return json.dumps({"answerable": answerable, "reason": reason, "sql": sql})


@pytest.fixture
def reader(connect_as: ConnectAs, nba_data: NbaData) -> psycopg.Connection:
    """A read-only connection to a database holding one game, with Luka Doncic in it."""
    nba_data.player(1629029, "Luka Doncic")
    nba_data.player(2, "Someone Else")
    nba_data.game("g1", "2025-26", game_date="2026-01-26")
    nba_data.stats("g1", 1629029, 1, won=True, pts=73, fgm=25, ftm=12)
    nba_data.stats("g1", 2, 2, won=False, pts=20, fgm=8, ftm=4)
    nba_data.conn.execute("UPDATE nba.meta SET latest_game_date = '2026-01-26'")
    return connect_as("nba_reader")


def answerer(llm: FakeLlm, conn: psycopg.Connection) -> TwoStepAnswerer:
    return TwoStepAnswerer(llm, conn, today=lambda: date(2026, 11, 23))


def test_answered_question(reader: psycopg.Connection) -> None:
    llm = FakeLlm(sql_reply(TOP_SCORER), "Luka Doncic scored 73 points.")

    answer = answerer(llm, reader).ask("  Who scored the most points in a game? ")

    assert answer.status == "answered"
    assert answer.text == "Luka Doncic scored 73 points."
    assert answer.question == "Who scored the most points in a game?"
    assert answer.sql == TOP_SCORER
    assert (answer.columns, answer.rows, answer.truncated) == (
        ["full_name", "pts"],
        [("Luka Doncic", 73)],
        False,
    )
    assert (answer.model, answer.llm_calls, answer.usage) == ("fake-model", 2, Usage(200, 20, 2))


def test_first_call_asks_for_sql_with_the_date_season_and_players(
    reader: psycopg.Connection,
) -> None:
    llm = FakeLlm(sql_reply(TOP_SCORER), "answer")

    answerer(llm, reader).ask("How many points did Luka Dončić score?")

    first = llm.calls[0]
    assert first["json_schema"] == SQL_REPLY_SCHEMA
    assert "TABLE nba.player_game_stats" in first["system"]
    assert "Today is 2026-11-23. The current season is 2026-27." in first["prompt"]
    assert "newest game in the database was played on 2026-01-26" in first["prompt"]
    assert (
        '"Luka Dončić": Luka Doncic, player_id 1629029, played 2025-26 to 2025-26'
        in first["prompt"]
    )


def test_second_call_gets_only_the_question_sql_and_rows(reader: psycopg.Connection) -> None:
    llm = FakeLlm(sql_reply(TOP_SCORER), "answer")

    answerer(llm, reader).ask("Who scored the most?")

    second = llm.calls[1]
    assert second["system"] == ANSWER_INSTRUCTIONS
    assert second["json_schema"] is None
    assert "Question: Who scored the most?" in second["prompt"]
    assert "Luka Doncic | 73" in second["prompt"]
    assert "TABLE nba" not in second["prompt"]


def test_refused_question_makes_one_call(reader: psycopg.Connection) -> None:
    llm = FakeLlm(sql_reply("", answerable=False, reason="Awards aren't in the data."))

    answer = answerer(llm, reader).ask("Who won MVP in 2023?")

    assert (answer.status, answer.text, answer.sql) == (
        "refused",
        "Awards aren't in the data.",
        None,
    )
    assert answer.llm_calls == 1


@pytest.mark.parametrize(
    "reply",
    ["not json", "[1, 2]", '{"answerable": true, "reason": "x"}', sql_reply("", answerable=True)],
)
def test_reply_in_the_wrong_shape_fails(reader: psycopg.Connection, reply: str) -> None:
    answer = answerer(FakeLlm(reply), reader).ask("q?")

    assert answer.status == "failed"
    assert "expected format" in answer.text


def test_unsafe_sql_is_refused_and_not_run(reader: psycopg.Connection) -> None:
    llm = FakeLlm(sql_reply("DELETE FROM players"))

    answer = answerer(llm, reader).ask("Delete everyone")

    assert answer.status == "failed"
    assert "safety check" in answer.text
    assert answer.sql == "DELETE FROM players"
    assert llm.calls[1:] == []


def test_database_error_fails_and_the_connection_still_works(reader: psycopg.Connection) -> None:
    llm = FakeLlm(sql_reply("SELECT no_such_column FROM players"), sql_reply(TOP_SCORER), "answer")
    ask = answerer(llm, reader)

    failed = ask.ask("q1?")
    worked = ask.ask("q2?")

    assert failed.status == "failed"
    assert "no_such_column" in failed.text
    assert worked.status == "answered"


def test_ai_unreachable_on_the_first_call(reader: psycopg.Connection) -> None:
    answer = answerer(FakeLlm(LlmQuotaError("HTTP 429: quota")), reader).ask("q?")

    assert answer.status == "failed"
    assert "couldn't be reached" in answer.text
    assert answer.llm_calls == 0


def test_ai_unreachable_on_the_second_call_keeps_the_rows(reader: psycopg.Connection) -> None:
    answer = answerer(FakeLlm(sql_reply(TOP_SCORER), LlmQuotaError("quota")), reader).ask("q?")

    assert answer.status == "failed"
    assert answer.rows == [("Luka Doncic", 73)]
    assert answer.llm_calls == 1


@pytest.mark.parametrize("question", ["", "   ", "x" * (MAX_QUESTION_LENGTH + 1)])
def test_empty_or_long_question_makes_no_calls(reader: psycopg.Connection, question: str) -> None:
    llm = FakeLlm()

    answer = answerer(llm, reader).ask(question)

    assert answer.status == "failed"
    assert llm.calls == []
