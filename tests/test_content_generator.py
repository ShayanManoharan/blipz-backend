import json

import pytest

from app.agents.content_generator import (
    normalize_trivia_question,
    normalize_trivia_questions,
    parse_trivia_questions,
)

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


# --- normalize_trivia_question(s): the Trivia grading fix's content validation ------
# generate_daily_content() used to trust an unconstrained free-text `answer` field with
# no validation at all — these pin down normalize_trivia_question(s) rejecting shapes
# that made correct id-based grading impossible to guarantee. See
# PRODUCTION_AUDIT.md's "Trivia grading fix".

VALID_RAW_QUESTION = {
    "question": "What is the capital of Japan?",
    "category": "geography",
    "options": ["Tokyo", "Beijing", "Seoul", "Bangkok"],
    "answer": "A",
}


def test_normalize_trivia_question_accepts_well_formed_input():
    result = normalize_trivia_question(VALID_RAW_QUESTION, index=2)
    assert result == {
        "id": "q2",
        "question": "What is the capital of Japan?",
        "category": "geography",
        "options": ["Tokyo", "Beijing", "Seoul", "Bangkok"],
        "correct_option_id": "A",
    }


def test_normalize_trivia_question_rejects_wrong_option_count():
    bad = {**VALID_RAW_QUESTION, "options": ["Tokyo", "Beijing", "Seoul"]}
    with pytest.raises(ValueError):
        normalize_trivia_question(bad, index=0)


def test_normalize_trivia_question_rejects_duplicate_options():
    bad = {**VALID_RAW_QUESTION, "options": ["Tokyo", "tokyo ", "Seoul", "Bangkok"]}
    with pytest.raises(ValueError):
        normalize_trivia_question(bad, index=0)


def test_normalize_trivia_question_rejects_empty_option_text():
    bad = {**VALID_RAW_QUESTION, "options": ["Tokyo", "", "Seoul", "Bangkok"]}
    with pytest.raises(ValueError):
        normalize_trivia_question(bad, index=0)


@pytest.mark.parametrize("bad_answer", ["E", "1", "", "AB", None])
def test_normalize_trivia_question_rejects_invalid_answer_letter(bad_answer):
    bad = {**VALID_RAW_QUESTION, "answer": bad_answer}
    with pytest.raises(ValueError):
        normalize_trivia_question(bad, index=0)


def test_normalize_trivia_question_rejects_missing_question_text():
    bad = {**VALID_RAW_QUESTION, "question": "   "}
    with pytest.raises(ValueError):
        normalize_trivia_question(bad, index=0)


def test_normalize_trivia_questions_rejects_wrong_question_count():
    with pytest.raises(ValueError):
        normalize_trivia_questions([VALID_RAW_QUESTION] * 4)


def test_normalize_trivia_questions_accepts_exactly_five():
    result = normalize_trivia_questions([VALID_RAW_QUESTION] * 5)
    assert len(result) == 5
    assert [q["id"] for q in result] == ["q0", "q1", "q2", "q3", "q4"]
