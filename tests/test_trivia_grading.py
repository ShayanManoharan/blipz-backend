# Regression tests for the Trivia letter-vs-option-text grading bug (see
# PRODUCTION_AUDIT.md's "Trivia grading fix"). The backend used to store the correct
# answer as an option letter ("A"/"B"/"C"/"D") while iOS submitted the full visible
# option TEXT (e.g. "Renegade") — `"Renegade" == "A"` never matched, so essentially
# every real (non-placeholder) Trivia attempt was graded wrong regardless of what the
# player picked. The fix: the client now submits a stable {question_id,
# selected_option_id} pair per question, and the backend grades purely by id.
#
# These tests use a controlled, hand-written trivia_questions payload (swapped into
# today's daily_content row for the duration of each test, then restored) instead of
# relying on today's AI-generated content, so option text/ids/correct answers are
# exactly known and assertions can be precise.

from contextlib import contextmanager
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.database import supabase
from app.main import app

REAL_TEST_USER_ID = "d366ce2a-6cbc-48b9-881c-a4560c9dadf5"


def _migration_applied() -> bool:
    try:
        supabase.table("scores").select("maths_completed").limit(1).execute()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _migration_applied(),
    reason="scores.maths_completed etc. not present — run sql/migrations.sql's latest block first",
)


def _today() -> str:
    return date.today().isoformat()


def _cleanup_scores():
    supabase.table("scores").delete().eq("user_id", REAL_TEST_USER_ID).eq("date", _today()).execute()


@pytest.fixture(autouse=True)
def _clean_scores_before_and_after():
    _cleanup_scores()
    yield
    _cleanup_scores()
    app.dependency_overrides.pop(get_current_user_id, None)


def _auth():
    app.dependency_overrides[get_current_user_id] = lambda: REAL_TEST_USER_ID


# q4 deliberately uses literal "A"/"B"/"C"/"D" as OPTION TEXT — the exact shape that
# historically let the pre-fix text-vs-letter bug accidentally "work" for placeholder
# content (submitted text "B" happened to equal the stored correct letter "B"). Mixing
# it in with normally-worded questions (q0-q3, where text and id never coincide) proves
# grading is genuinely id-based rather than accidentally text-based for every question,
# not just the placeholder-shaped one.
CONTROLLED_QUESTIONS = [
    {
        "id": "q0", "question": "Which city is the capital of France?", "category": "geo",
        "options": ["Paris", "Lyon", "Nice", "Marseille"], "correct_option_id": "A",
    },
    {
        "id": "q1", "question": "What is 2 + 2?", "category": "math",
        "options": ["3", "4", "5", "6"], "correct_option_id": "B",
    },
    {
        "id": "q2", "question": "Which is the largest planet in our solar system?", "category": "science",
        "options": ["Earth", "Mars", "Jupiter", "Venus"], "correct_option_id": "C",
    },
    {
        "id": "q3", "question": "Which ocean lies between the US and Europe?", "category": "geo",
        "options": ["Pacific", "Indian", "Arctic", "Atlantic"], "correct_option_id": "D",
    },
    {
        "id": "q4", "question": "Placeholder-lettered question", "category": "misc",
        "options": ["A", "B", "C", "D"], "correct_option_id": "B",
    },
]

ALL_CORRECT = {"q0": "A", "q1": "B", "q2": "C", "q3": "D", "q4": "B"}
ALL_WRONG = {"q0": "B", "q1": "A", "q2": "A", "q3": "A", "q4": "A"}


@contextmanager
def _controlled_trivia_content():
    today = _today()
    existing = supabase.table("daily_content").select("trivia_questions").eq("date", today).execute()
    original = existing.data[0]["trivia_questions"] if existing.data else None
    supabase.table("daily_content").update({"trivia_questions": CONTROLLED_QUESTIONS}).eq("date", today).execute()
    try:
        yield
    finally:
        if original is not None:
            supabase.table("daily_content").update({"trivia_questions": original}).eq("date", today).execute()


def _answers(selections: dict) -> list:
    return [{"question_id": qid, "selected_option_id": opt} for qid, opt in selections.items()]


# --- Correctness is id-based, not text-based --------------------------------------


def test_all_five_correct_options_score_5_of_5():
    _auth()
    with _controlled_trivia_content(), TestClient(app) as client:
        response = client.post("/games/submit-trivia", json={"answers": _answers(ALL_CORRECT)})
    assert response.status_code == 200
    body = response.json()
    assert body["correct"] == 5
    assert body["total"] == 5


def test_all_five_wrong_options_score_0_of_5_but_still_marks_completed():
    _auth()
    with _controlled_trivia_content(), TestClient(app) as client:
        response = client.post("/games/submit-trivia", json={"answers": _answers(ALL_WRONG)})
    assert response.status_code == 200
    assert response.json()["correct"] == 0

    row = supabase.table("scores").select("trivia_completed, trivia_score").eq(
        "user_id", REAL_TEST_USER_ID
    ).eq("date", _today()).execute().data[0]
    assert row["trivia_completed"] is True
    assert row["trivia_score"] == 0


def test_one_wrong_answer_costs_exactly_one_point():
    # Proves grading is per-question, not all-or-nothing — a partial score is possible
    # and each question's correctness is independent of the others.
    _auth()
    selections = {**ALL_CORRECT, "q2": "A"}  # only q2 wrong; A is Earth, not Jupiter
    with _controlled_trivia_content(), TestClient(app) as client:
        response = client.post("/games/submit-trivia", json={"answers": _answers(selections)})
    assert response.status_code == 200
    assert response.json()["correct"] == 4


def test_placeholder_lettered_question_grades_by_id_not_by_coincidental_text_match():
    # q4's options are literally the strings "A"/"B"/"C"/"D" — the pre-fix bug compared
    # submitted TEXT against a stored LETTER, so a submission of text "B" would have
    # accidentally scored correct for this exact shape (text "B" == stored answer "B"),
    # masking the fact that grading was fundamentally broken for every other question.
    # Here we submit selected_option_id "A" for q4 (wrong; correct id is "B") while every
    # other question is correct — if grading were still comparing raw text, this would
    # coincidentally look plausible; comparing ids catches it as wrong.
    _auth()
    selections = {**ALL_CORRECT, "q4": "A"}
    with _controlled_trivia_content(), TestClient(app) as client:
        response = client.post("/games/submit-trivia", json={"answers": _answers(selections)})
    assert response.status_code == 200
    assert response.json()["correct"] == 4


def test_option_text_submitted_as_selected_option_id_is_rejected():
    # Submitting the visible option TEXT ("Paris") where an identifier is expected must
    # be rejected at the schema layer — proves text and id can never be confused.
    _auth()
    selections = {**ALL_CORRECT, "q0": "Paris"}
    with _controlled_trivia_content(), TestClient(app) as client:
        response = client.post("/games/submit-trivia", json={"answers": _answers(selections)})
    assert response.status_code == 422


# --- Validation: malformed/missing/duplicate/unknown identifiers ------------------


@pytest.mark.parametrize("bad_option_id", ["E", "a", "1", "", "AB", "AA"])
def test_malformed_option_id_rejected(bad_option_id):
    _auth()
    selections = {**ALL_CORRECT, "q0": bad_option_id}
    with _controlled_trivia_content(), TestClient(app) as client:
        response = client.post("/games/submit-trivia", json={"answers": _answers(selections)})
    assert response.status_code == 422


def test_missing_question_id_rejected():
    _auth()
    selections = dict(ALL_CORRECT)
    del selections["q3"]  # only 4 of 5 required questions answered
    with _controlled_trivia_content(), TestClient(app) as client:
        response = client.post("/games/submit-trivia", json={"answers": _answers(selections)})
    assert response.status_code == 400


def test_duplicate_question_id_rejected():
    _auth()
    answers = _answers({k: v for k, v in ALL_CORRECT.items() if k != "q3"})
    answers.append({"question_id": "q0", "selected_option_id": "B"})  # q0 submitted twice, q3 missing
    with _controlled_trivia_content(), TestClient(app) as client:
        response = client.post("/games/submit-trivia", json={"answers": answers})
    assert response.status_code == 400


def test_unknown_question_id_rejected():
    _auth()
    answers = _answers({k: v for k, v in ALL_CORRECT.items() if k != "q3"})
    answers.append({"question_id": "not-a-real-question", "selected_option_id": "A"})
    with _controlled_trivia_content(), TestClient(app) as client:
        response = client.post("/games/submit-trivia", json={"answers": answers})
    assert response.status_code == 400


def test_extra_question_id_rejected():
    _auth()
    answers = _answers(ALL_CORRECT)
    answers.append({"question_id": "q5-does-not-exist", "selected_option_id": "A"})
    with _controlled_trivia_content(), TestClient(app) as client:
        response = client.post("/games/submit-trivia", json={"answers": answers})
    assert response.status_code == 400


# --- Public payload never leaks correct answers ------------------------------------


def test_correct_answers_absent_from_controlled_daily_content():
    _auth()
    with _controlled_trivia_content(), TestClient(app) as client:
        response = client.get("/games/daily-content")
    assert response.status_code == 200
    for question in response.json()["trivia_questions"]:
        assert "correct_option_id" not in question
        assert "answer" not in question
        assert set(question.keys()) == {"id", "question", "category", "options"}


# --- Review: unavailable before completion, accurate after ------------------------


def test_trivia_review_unavailable_before_completion_controlled():
    _auth()
    with _controlled_trivia_content(), TestClient(app) as client:
        response = client.get("/games/trivia-review")
    assert response.status_code == 404


def test_trivia_review_accurate_after_completion():
    _auth()
    selections = {**ALL_CORRECT, "q2": "A"}  # q2 deliberately wrong (picks "Earth")
    with _controlled_trivia_content(), TestClient(app) as client:
        client.post("/games/submit-trivia", json={"answers": _answers(selections)})
        response = client.get("/games/trivia-review")

    assert response.status_code == 200
    review = {item["question"]: item for item in response.json()["review"]}

    q2 = review["Which is the largest planet in our solar system?"]
    assert q2["selected_option_id"] == "A"
    assert q2["selected_answer_text"] == "Earth"
    assert q2["correct_option_id"] == "C"
    assert q2["correct_answer_text"] == "Jupiter"
    assert q2["is_correct"] is False

    q0 = review["Which city is the capital of France?"]
    assert q0["selected_option_id"] == "A"
    assert q0["selected_answer_text"] == "Paris"
    assert q0["correct_option_id"] == "A"
    assert q0["correct_answer_text"] == "Paris"
    assert q0["is_correct"] is True


# --- Idempotent resubmission --------------------------------------------------------


def test_repeated_trivia_submission_returns_original_stored_result():
    _auth()
    with _controlled_trivia_content(), TestClient(app) as client:
        first = client.post("/games/submit-trivia", json={"answers": _answers(ALL_WRONG)}).json()
        second = client.post("/games/submit-trivia", json={"answers": _answers(ALL_CORRECT)}).json()

    assert first["already_completed"] is False
    assert first["correct"] == 0
    assert second["already_completed"] is True
    assert second["correct"] == 0  # NOT recalculated to 5 despite now-correct answers

    row = supabase.table("scores").select("trivia_score").eq("user_id", REAL_TEST_USER_ID).eq(
        "date", _today()
    ).execute().data[0]
    assert row["trivia_score"] == 0
