import json

import pytest

from app.agents.content_generator import parse_trivia_questions

SAMPLE = [{"question": "q", "category": "science", "options": ["A", "B"], "answer": "A"}]


def test_parse_trivia_questions_clean_json():
    assert parse_trivia_questions(json.dumps(SAMPLE)) == SAMPLE


def test_parse_trivia_questions_code_fenced():
    text = "```json\n" + json.dumps(SAMPLE) + "\n```"
    assert parse_trivia_questions(text) == SAMPLE


def test_parse_trivia_questions_with_stray_prose():
    text = "Here you go:\n" + json.dumps(SAMPLE) + "\nHope that helps!"
    assert parse_trivia_questions(text) == SAMPLE


def test_parse_trivia_questions_unparsable_raises():
    with pytest.raises(ValueError):
        parse_trivia_questions("not json at all")
