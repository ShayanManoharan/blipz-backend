import json

import pytest

from app.agents.content_generator import (
    compute_math_answer,
    generate_math_problems,
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


# --- generate_math_problems: no negative subtraction answers ------------------------
# random.randint draws for subtraction's two operands used to be independent, so the
# smaller one could land first (e.g. 30 - 50). Regenerating many 20-problem sets and
# checking every subtract problem in every set is the only way to catch this reliably —
# a single generated set can easily avoid the bad ordering by chance.

MATH_SET_SAMPLE_COUNT = 200


def test_generate_math_problems_never_produces_negative_subtraction():
    for _ in range(MATH_SET_SAMPLE_COUNT):
        problems = generate_math_problems(20)
        for problem in problems:
            if problem["operation"] != "subtract":
                continue
            answer = compute_math_answer(problem["left_operand"], problem["right_operand"], problem["operation"])
            assert answer >= 0, f"negative subtraction answer: {problem}"


def test_generate_math_problems_subtraction_displays_larger_operand_first():
    for _ in range(MATH_SET_SAMPLE_COUNT):
        problems = generate_math_problems(20)
        for problem in problems:
            if problem["operation"] != "subtract":
                continue
            assert problem["left_operand"] >= problem["right_operand"], f"smaller operand shown first: {problem}"


def test_generate_math_problems_division_is_integer_and_never_by_zero():
    for _ in range(MATH_SET_SAMPLE_COUNT):
        problems = generate_math_problems(20)
        for problem in problems:
            if problem["operation"] != "divide":
                continue
            assert problem["right_operand"] != 0
            left, right = problem["left_operand"], problem["right_operand"]
            assert left % right == 0, f"non-integer division: {problem}"
            assert compute_math_answer(left, right, "divide") == left // right


def test_generate_math_problems_produces_full_set_with_no_overall_negative_answers():
    for _ in range(MATH_SET_SAMPLE_COUNT):
        problems = generate_math_problems(20)
        assert len(problems) == 20
        for problem in problems:
            answer = compute_math_answer(problem["left_operand"], problem["right_operand"], problem["operation"])
            assert answer >= 0, f"negative answer in daily set: {problem}"
