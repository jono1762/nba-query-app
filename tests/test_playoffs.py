"""Inferring playoff rounds from who played whom (no network, no database)."""

from datetime import date, timedelta

import pytest

from nba_query_app.ingest.playoffs import PlayoffGame, playoff_rounds, round_name

START = date(2016, 4, 16)


class Bracket:
    """Builds playoff games: series(day, winner, loser, "4-3") adds that many games."""

    def __init__(self) -> None:
        self.rows: list[PlayoffGame] = []

    def series(self, day: int, winner: int, loser: int, score: str) -> list[str]:
        wins, losses = map(int, score.split("-"))
        results = [True] * wins + [False] * losses
        results.insert(0, results.pop())  # vary the order a little
        ids = []
        for i, winner_won in enumerate(results):
            game_id = f"g{len(self.rows) // 2:03d}"
            when = START + timedelta(days=day + i)
            self.rows += [
                PlayoffGame(game_id, when, winner, winner_won),
                PlayoffGame(game_id, when, loser, not winner_won),
            ]
            ids.append(game_id)
        return ids


def rounds_by_series(result: dict, ids: list[str]) -> set[tuple[int, str]]:
    return {(result[i].round, result[i].round_name) for i in ids}


def test_four_team_bracket_counts_back_from_the_finals() -> None:
    b = Bracket()
    semi_1 = b.series(0, 1, 2, "4-1")
    semi_2 = b.series(0, 3, 4, "4-3")
    finals = b.series(10, 3, 1, "4-2")

    result = playoff_rounds("2015-16", b.rows)

    assert rounds_by_series(result, semi_1) == {(1, "Conference Finals")}
    assert rounds_by_series(result, semi_2) == {(1, "Conference Finals")}
    assert rounds_by_series(result, finals) == {(2, "Finals")}
    assert [result[i].series_game for i in finals] == [1, 2, 3, 4, 5, 6]


def test_a_team_with_a_bye_starts_in_the_second_round() -> None:
    # 1974-75 style: the top seeds skip the first round.
    b = Bracket()
    first = b.series(0, 3, 5, "2-1")  # team 3 beats 5 to reach the bye team 1
    semi_a = b.series(5, 1, 3, "4-2")
    semi_b = b.series(5, 2, 4, "4-0")  # 2 and 4 both had byes
    finals = b.series(15, 1, 2, "4-3")

    result = playoff_rounds("1974-75", b.rows)

    assert rounds_by_series(result, first) == {(1, "Conference Semifinals")}
    assert rounds_by_series(result, semi_a) == {(2, "Conference Finals")}
    assert rounds_by_series(result, semi_b) == {(2, "Conference Finals")}
    assert rounds_by_series(result, finals) == {(3, "Finals")}


def test_playoffs_in_progress_count_forward() -> None:
    b = Bracket()
    first_a = b.series(0, 1, 2, "4-0")
    first_b = b.series(0, 3, 4, "4-2")
    first_c = b.series(0, 5, 6, "3-2")  # still going
    second = b.series(8, 1, 3, "1-1")  # started, tied

    result = playoff_rounds("2025-26", b.rows)

    for ids in (first_a, first_b, first_c):
        assert rounds_by_series(result, ids) == {(1, "First Round")}
    assert rounds_by_series(result, second) == {(2, "Conference Semifinals")}


def test_a_loser_playing_on_is_not_inferred() -> None:
    # A round robin not listed in ROUND_ROBIN_LAST_DAY: teams lost and played on.
    b = Bracket()
    b.series(0, 1, 2, "2-0")
    b.series(2, 1, 3, "2-0")
    b.series(4, 3, 2, "2-0")
    b.series(10, 1, 3, "4-1")

    assert playoff_rounds("1960-61", b.rows) is None


def test_overlapping_series_are_not_inferred() -> None:
    # A team's games against two others interleave, as in a round robin.
    b = Bracket()
    b.series(0, 1, 2, "2-0")
    b.series(1, 3, 2, "2-0")  # team 2 again, while its first pairing is still going

    assert playoff_rounds("1960-61", b.rows) is None


def test_1953_54_round_robin_then_knockout() -> None:
    # The real shape: a round robin in each division (ends 1954-03-22), then division
    # finals in which two pairs met again, then the Finals.
    syr, bos, nyk, mnl, roc, ftw = 1, 2, 3, 4, 5, 6
    rows: list[PlayoffGame] = []

    def game(game_id: str, day: date, winner: int, loser: int) -> None:
        rows.extend(
            [PlayoffGame(game_id, day, winner, True), PlayoffGame(game_id, day, loser, False)]
        )

    march = lambda d: date(1954, 3, d)  # noqa: E731
    game("rr1", march(16), bos, nyk)
    game("rr2", march(17), syr, bos)
    game("rr3", march(18), syr, nyk)
    game("rr4", march(17), mnl, roc)
    game("rr5", march(18), mnl, ftw)
    game("rr6", march(22), syr, bos)
    game("df0", march(24), mnl, roc)
    game("df1", march(25), syr, bos)
    game("df2", march(27), syr, bos)
    game("df3", march(27), roc, mnl)
    game("df4", march(28), mnl, roc)
    game("f1", march(31), mnl, syr)
    game("f2", date(1954, 4, 3), syr, mnl)
    game("f3", date(1954, 4, 4), mnl, syr)

    result = playoff_rounds("1953-54", rows)

    assert rounds_by_series(result, [f"rr{i}" for i in range(1, 7)]) == {(1, "Round Robin")}
    assert rounds_by_series(result, ["df0", "df1", "df2", "df3", "df4"]) == {(2, "Division Finals")}
    assert rounds_by_series(result, ["f1", "f2", "f3"]) == {(3, "Finals")}
    assert [result[i].series_game for i in ("rr2", "rr6", "df1", "df2")] == [1, 2, 1, 2]


def test_no_playoff_games_means_no_rounds() -> None:
    assert playoff_rounds("2026-27", []) == {}


@pytest.mark.parametrize(
    ("before_finals", "season", "name"),
    [
        (0, "1960-61", "Finals"),
        (1, "1969-70", "Division Finals"),
        (1, "1970-71", "Conference Finals"),
        (2, "1966-67", "Division Semifinals"),
        (3, "1983-84", "First Round"),
    ],
)
def test_round_names(before_finals: int, season: str, name: str) -> None:
    assert round_name(before_finals, season) == name
