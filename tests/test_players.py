"""Finding the players a question names."""

from conftest import NbaData

from nba_query_app.query.players import PlayerMatch, PlayerNames, normalize, with_seasons

PLAYERS = [
    (203999, "Nikola Jokic"),
    (1629029, "Luka Doncic"),
    (2544, "LeBron James"),
    (201935, "James Harden"),
    (698, "Eddie Johnson"),
    (77144, "Eddie Johnson"),
    (76003, "Kareem Abdul-Jabbar"),
    (406, "Shaquille O'Neal"),
    (1, "Mike James"),
    (2, "Bernard James"),
    (3, "Jerome James"),
    (4, "Damion James"),
    (5, "Justin James"),
    (1630527, "Brandon Boston"),
]
TEAMS = ["Boston Celtics", "Los Angeles Lakers"]


def found(question: str) -> list[tuple[str, int]]:
    return [(m.mentioned_as, m.player_id) for m in PlayerNames(PLAYERS, TEAMS).find(question)]


def test_normalize_drops_accents_case_and_punctuation() -> None:
    assert normalize("Dončić O'Neal Abdul-Jabbar") == ["doncic", "oneal", "abdul", "jabbar"]


def test_accents_typed_in_the_question_are_ignored() -> None:
    # Stored names have no accents (names.py); someone may still type them.
    assert found("How many 50-point games has Luka Dončić had?") == [("Luka Dončić", 1629029)]


def test_lowercase_and_punctuation_in_the_question() -> None:
    assert found("shaquille oneal vs kareem abdul jabbar") == [
        ("kareem abdul jabbar", 76003),
        ("shaquille oneal", 406),
    ]
    assert found("shaquille o'neal's free throws") == [("shaquille o'neal's", 406)]


def test_shared_name_returns_every_player() -> None:
    assert found("Eddie Johnson career points") == [
        ("Eddie Johnson", 698),
        ("Eddie Johnson", 77144),
    ]


def test_last_name_alone_when_few_players_have_it() -> None:
    assert found("Jokic's triple-doubles in 2024-25") == [("Jokic's", 203999)]


def test_common_last_name_alone_is_ignored() -> None:
    # Seven players named James here, more than MAX_SURNAME_MATCHES.
    assert found("Who scored more, James or Jokic?") == [("Jokic", 203999)]


def test_last_name_must_be_capitalised() -> None:
    assert found("what about harden") == []


def test_a_full_name_isnt_also_matched_by_its_last_name() -> None:
    assert found("LeBron James and James Harden") == [
        ("LeBron James", 2544),
        ("James Harden", 201935),
    ]


def test_a_team_name_isnt_a_last_name() -> None:
    assert found("Most points against the Boston Celtics") == []
    assert found("Brandon Boston against the Celtics") == [("Brandon Boston", 1630527)]


def test_no_players() -> None:
    assert found("Which team won the most games this season?") == []


def test_load_and_seasons_from_the_database(nba_data: NbaData) -> None:
    nba_data.player(203999, "Nikola Jokic")
    nba_data.game("g1", "2015-16")
    nba_data.game("g2", "2024-25")
    nba_data.stats("g1", 203999, 1, won=True)
    nba_data.stats("g2", 203999, 1, won=True)

    matches = PlayerNames.load(nba_data.conn).find("Nikola Jokic")

    assert with_seasons(nba_data.conn, matches) == [
        PlayerMatch("Nikola Jokic", 203999, "Nikola Jokic", "2015-16", "2024-25")
    ]
