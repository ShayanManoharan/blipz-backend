import pytest

from app.agents.guess_scorer import parse_score


@pytest.mark.parametrize("text,expected", [
    ("7.5", 7.5),
    ("Score: 7.5", 7.5),
    ("The score is 9 out of 10", 9.0),
    ("15", 10.0),  # clamped to max
    ("-3", 0.0),  # clamped to min
    ("no number here", 0.0),  # fallback when nothing parses
])
def test_parse_score(text, expected):
    assert parse_score(text) == expected
