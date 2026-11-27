"""Answer a question: the AI writes SQL, we check and run it, the AI phrases the answer.

`Answerer` is the interface the rest of the app uses: a question in, an `Answer`
out (the plain-English answer, the rows, the SQL, and the AI calls and tokens
used). `TwoStepAnswerer` is the base strategy (see docs/PLAN.md step 12):

1. Build the instructions (prompt.py), with today's date, the current season and
   the newest game.
2. AI call 1 writes the SQL, or says the question can't be answered.
3. The SQL is safety-checked and run read-only (run.py).
4. AI call 2 phrases the answer from the result rows only.

A different strategy (e.g. an agent that may run several queries) can replace it
without touching the callers. Failures come back as an Answer with status
"failed" rather than an exception, so the calls and tokens used are still known.
Retrying a failed query is step 13.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol

import psycopg

from nba_query_app.llm.client import NO_USAGE, LlmClient, LlmError, LlmReply, Usage
from nba_query_app.query.prompt import (
    ANSWER_INSTRUCTIONS,
    SQL_REPLY_SCHEMA,
    QuestionContext,
    answer_request,
    describe_schema,
    sql_instructions,
    sql_request,
)
from nba_query_app.query.run import QueryTimeoutError, run_query
from nba_query_app.query.safety import UnsafeSqlError
from nba_query_app.seasons import season_for_date

MAX_QUESTION_LENGTH = 500

Status = Literal["answered", "refused", "failed"]


@dataclass(frozen=True)
class Answer:
    question: str
    status: Status
    # answered: the plain-English answer; refused: why; failed: what went wrong.
    text: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    truncated: bool = False  # the query had more rows than were fetched
    model: str | None = None
    llm_calls: int = 0
    usage: Usage = NO_USAGE
    seconds: float = 0.0


class Answerer(Protocol):
    def ask(self, question: str) -> Answer: ...


class TwoStepAnswerer:
    """`conn` must be a read-only user's connection (see run.py), not in a transaction."""

    def __init__(
        self,
        llm: LlmClient,
        conn: psycopg.Connection,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._llm = llm
        self._conn = conn
        self._today = today
        self._instructions: str | None = None  # read from the database on first use

    def ask(self, question: str) -> Answer:
        started = time.perf_counter()
        question = " ".join(question.split())
        if not question:
            return Answer(question, "failed", "Please ask a question.")
        if len(question) > MAX_QUESTION_LENGTH:
            return Answer(
                question, "failed", f"Please keep questions under {MAX_QUESTION_LENGTH} characters."
            )

        calls: list[LlmReply] = []

        def finish(status: Status, text: str, **rest: Any) -> Answer:
            return Answer(
                question,
                status,
                text,
                model=calls[-1].model if calls else None,
                llm_calls=len(calls),
                usage=sum((c.usage for c in calls), NO_USAGE),
                seconds=time.perf_counter() - started,
                **rest,
            )

        try:
            calls.append(
                self._llm.generate(
                    system=self._sql_instructions(),
                    prompt=sql_request(question, self._context()),
                    json_schema=SQL_REPLY_SCHEMA,
                )
            )
        except LlmError as error:
            return finish("failed", f"The AI couldn't be reached: {error}")

        reply = _parse_sql_reply(calls[-1].text)
        if reply is None:
            return finish("failed", "The AI's reply wasn't in the expected format.")
        answerable, reason, sql = reply
        if not answerable:
            return finish("refused", reason or "That question can't be answered from this data.")

        try:
            result = run_query(self._conn, sql)
        except UnsafeSqlError as error:
            return finish("failed", f"The query was refused by the safety check: {error}", sql=sql)
        except QueryTimeoutError as error:
            return finish("failed", str(error), sql=sql)
        except psycopg.Error as error:
            first_line = str(error).splitlines()[0] if str(error) else type(error).__name__
            return finish("failed", f"The query failed: {first_line}", sql=sql)

        found = {
            "sql": sql,
            "columns": result.columns,
            "rows": result.rows,
            "truncated": result.truncated,
        }
        try:
            calls.append(
                self._llm.generate(
                    system=ANSWER_INSTRUCTIONS,
                    prompt=answer_request(question, sql, result, note=reason),
                )
            )
        except LlmError as error:
            return finish("failed", f"The AI couldn't phrase the answer: {error}", **found)
        return finish("answered", calls[-1].text.strip(), **found)

    def _sql_instructions(self) -> str:
        if self._instructions is None:
            self._instructions = sql_instructions(describe_schema(self._conn))
        return self._instructions

    def _context(self) -> QuestionContext:
        row = self._conn.execute("SELECT latest_game_date FROM nba.meta").fetchone()
        self._conn.rollback()  # leave no transaction open (run_query needs that)
        today = self._today()
        return QuestionContext(today, season_for_date(today), row[0] if row else None)


def _parse_sql_reply(text: str) -> tuple[bool, str, str] | None:
    """(answerable, reason, sql), or None if the reply isn't the agreed shape."""
    try:
        reply = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(reply, dict):
        return None
    answerable, reason, sql = reply.get("answerable"), reply.get("reason"), reply.get("sql")
    if not isinstance(answerable, bool) or not isinstance(reason, str) or not isinstance(sql, str):
        return None
    if answerable and not sql.strip():
        return None
    return answerable, reason.strip(), sql.strip()
