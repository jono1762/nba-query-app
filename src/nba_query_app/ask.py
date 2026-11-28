"""Ask a question from the command line, against the real AI and database.

    uv run --env-file .env python -m nba_query_app.ask "Who scored the most points in 2025-26?"

Needs READER_DATABASE_URL (a read-only login, see README) and GEMINI_API_KEY. Each
question makes up to two calls to the AI, from the free allowance. Prints the answer,
then the SQL, the rows and the AI usage. The exit code is 0 if the question was
answered or refused, 1 if something failed.
"""

import argparse
import os
import sys

import psycopg

from nba_query_app.llm.gemini import GeminiClient
from nba_query_app.query.ask import Answer, TwoStepAnswerer

ROWS_SHOWN = 20


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask an NBA stats question.")
    parser.add_argument("question", help="the question, in quotes")
    args = parser.parse_args(argv)

    db_url = os.environ.get("READER_DATABASE_URL")
    if not db_url:
        print("READER_DATABASE_URL is not set (run with --env-file .env)", file=sys.stderr)
        return 1
    with psycopg.connect(db_url, autocommit=True) as conn:
        answer = TwoStepAnswerer(GeminiClient.from_env(), conn).ask(args.question)
    print(format_answer(answer))
    return 1 if answer.status == "failed" else 0


def format_answer(answer: Answer) -> str:
    label = {"answered": "", "refused": "Can't answer: ", "failed": "Failed: "}[answer.status]
    lines = [f"{label}{answer.text}"]
    if answer.sql:
        lines += ["", "SQL:", answer.sql]
    if answer.columns:
        lines += ["", " | ".join(answer.columns)]
        lines += [
            " | ".join("NULL" if v is None else str(v) for v in r) for r in answer.rows[:ROWS_SHOWN]
        ]
        hidden = len(answer.rows) - ROWS_SHOWN
        if hidden > 0 or answer.truncated:
            lines.append(f"... {f'{hidden} more rows' if hidden > 0 else 'more rows'} not shown")
    u = answer.usage
    lines += [
        "",
        f"{answer.model or 'no AI call'}: {answer.llm_calls} call(s), "
        f"{u.input_tokens:,} tokens in, {u.output_tokens:,} out, "
        f"{u.thinking_tokens:,} thinking; {answer.seconds:.1f} s",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
