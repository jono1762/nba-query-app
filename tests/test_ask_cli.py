"""The command-line output of `python -m nba_query_app.ask`."""

from nba_query_app.ask import ROWS_SHOWN, format_answer
from nba_query_app.llm.client import Usage
from nba_query_app.query.ask import Answer


def test_answered() -> None:
    answer = Answer(
        "q",
        "answered",
        "Wilt scored 100.",
        sql="SELECT 1",
        columns=["full_name", "pts"],
        rows=[("Wilt Chamberlain", 100), ("Nobody", None)],
        model="m",
        llm_calls=2,
        usage=Usage(5000, 100, 0),
        seconds=3.21,
    )

    assert format_answer(answer).splitlines() == [
        "Wilt scored 100.",
        "",
        "SQL:",
        "SELECT 1",
        "",
        "full_name | pts",
        "Wilt Chamberlain | 100",
        "Nobody | NULL",
        "",
        "m: 2 call(s), 5,000 tokens in, 100 out, 0 thinking; 3.2 s",
    ]


def test_refused_and_failed_are_labelled() -> None:
    assert format_answer(Answer("q", "refused", "No awards.")).startswith(
        "Can't answer: No awards."
    )
    assert format_answer(Answer("q", "failed", "Boom.")).startswith("Failed: Boom.")


def test_long_results_are_cut_short() -> None:
    rows = [(i,) for i in range(ROWS_SHOWN + 3)]

    text = format_answer(Answer("q", "answered", "a", columns=["n"], rows=rows, truncated=True))

    assert "... 3 more rows not shown" in text
