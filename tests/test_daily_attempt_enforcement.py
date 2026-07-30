# Tests for PRODUCTION_AUDIT.md B2 and its fix: one ranked attempt per user/game/day,
# enforced via explicit persisted completion state (never score > 0 / mathsScore == 20
# heuristics), idempotent replay on resubmission, race-safe concurrent submission, a
# genuine zero score still counting as completed, Guess re-roll/rate-limit protection,
# and the Trivia post-submission review endpoint.
#
# Requires the migration in sql/migrations.sql (maths_completed/guess_completed/
# trivia_completed/maths_elapsed_seconds/guess_text/trivia_answers on `scores`) to be
# applied — these tests are skipped entirely if it isn't, rather than failing noisily
# on an environment that just hasn't been migrated yet.

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest.mock import patch

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


def _cleanup(user_id: str = REAL_TEST_USER_ID):
    today = date.today().isoformat()
    supabase.table("scores").delete().eq("user_id", user_id).eq("date", today).execute()


@pytest.fixture(autouse=True)
def _clean_before_and_after():
    _cleanup()
    yield
    _cleanup()
    app.dependency_overrides.pop(get_current_user_id, None)


def _auth_as(user_id: str = REAL_TEST_USER_ID):
    app.dependency_overrides[get_current_user_id] = lambda: user_id


# --- Maths: first submission stored, resubmission is idempotent ------------------


def test_first_maths_submission_is_stored():
    _auth_as()
    with TestClient(app) as client:
        content = client.get("/games/daily-content").json()
        problems = content["math_problems"]
        answers = [_answer_for(p) for p in problems]
        response = client.post(
            "/games/submit-maths", json={"answers": answers, "elapsed_seconds": 12.0}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["already_completed"] is False
    assert body["correct"] == len(problems)

    row = supabase.table("scores").select("*").eq("user_id", REAL_TEST_USER_ID).eq(
        "date", date.today().isoformat()
    ).execute().data[0]
    assert row["maths_completed"] is True
    assert row["maths_score"] == len(problems)


def test_second_maths_submission_returns_original_result_not_a_recalculation():
    _auth_as()
    with TestClient(app) as client:
        content = client.get("/games/daily-content").json()
        problems = content["math_problems"]
        correct_answers = [_answer_for(p) for p in problems]

        first = client.post(
            "/games/submit-maths", json={"answers": correct_answers, "elapsed_seconds": 12.0}
        ).json()

        # Second call sends deliberately WRONG answers — if the endpoint recalculated,
        # this would lower the stored score. It must not.
        wrong_answers = [a + 1 for a in correct_answers]
        second = client.post(
            "/games/submit-maths", json={"answers": wrong_answers, "elapsed_seconds": 5.0}
        ).json()

    assert first["already_completed"] is False
    assert second["already_completed"] is True
    assert second["correct"] == first["correct"] == len(problems)


def test_maths_answers_length_mismatch_rejected():
    _auth_as()
    with TestClient(app) as client:
        response = client.post(
            "/games/submit-maths", json={"answers": [1, 2, 3], "elapsed_seconds": 5.0}
        )
    assert response.status_code == 400


def test_maths_implausible_elapsed_time_rejected():
    _auth_as()
    with TestClient(app) as client:
        content = client.get("/games/daily-content").json()
        answers = [_answer_for(p) for p in content["math_problems"]]
        response = client.post(
            "/games/submit-maths", json={"answers": answers, "elapsed_seconds": 0.0}
        )
    assert response.status_code == 400


# --- Guess: no re-roll, no repeated OpenAI charge ---------------------------------


@patch("app.routers.games.score_guess")
def test_second_guess_submission_does_not_call_openai_again(mock_score_guess):
    mock_score_guess.return_value = 7.0
    _auth_as()
    with TestClient(app) as client:
        first = client.post("/games/submit-guess", json={"guess": "a spaceship"}).json()
        second = client.post("/games/submit-guess", json={"guess": "something totally different"}).json()

    assert first["already_completed"] is False
    assert second["already_completed"] is True
    assert second["score"] == first["score"]
    mock_score_guess.assert_called_once()  # not called again for the second request


def test_empty_guess_rejected():
    _auth_as()
    with TestClient(app) as client:
        response = client.post("/games/submit-guess", json={"guess": ""})
    assert response.status_code == 422


def test_excessively_long_guess_rejected():
    _auth_as()
    with TestClient(app) as client:
        response = client.post("/games/submit-guess", json={"guess": "x" * 500})
    assert response.status_code == 422


@patch("app.routers.games.score_guess")
def test_guess_rate_limit_enforced(mock_score_guess):
    mock_score_guess.return_value = 5.0
    _auth_as()
    with TestClient(app) as client:
        statuses = []
        for i in range(12):
            response = client.post("/games/submit-guess", json={"guess": f"guess number {i}"})
            statuses.append(response.status_code)
            _cleanup()  # clear completion between calls so we're testing the rate limiter, not idempotency

    assert 429 in statuses, f"expected a 429 among {statuses} after exceeding the configured limit"


# --- Genuine zero score still counts as completed ---------------------------------


def test_genuine_zero_trivia_score_is_still_marked_completed():
    _auth_as()
    with TestClient(app) as client:
        content = client.get("/games/daily-content").json()
        questions = content["trivia_questions"]
        # Deliberately wrong answers for every question — a legitimate all-wrong run.
        wrong_answers = ["not a real option"] * len(questions)
        response = client.post("/games/submit-trivia", json={"answers": wrong_answers})

    assert response.status_code == 200
    body = response.json()
    assert body["correct"] == 0

    row = supabase.table("scores").select("trivia_completed, trivia_score").eq(
        "user_id", REAL_TEST_USER_ID
    ).eq("date", date.today().isoformat()).execute().data[0]
    assert row["trivia_completed"] is True
    assert row["trivia_score"] == 0


# --- Concurrency: duplicate concurrent submissions cannot create two ranked attempts


def test_concurrent_maths_submissions_do_not_create_two_ranked_attempts():
    _auth_as()
    with TestClient(app) as client:
        content = client.get("/games/daily-content").json()
        problems = content["math_problems"]
        all_correct = [_answer_for(p) for p in problems]
        all_wrong = [a + 1 for a in all_correct]

        def submit(answers):
            return client.post(
                "/games/submit-maths", json={"answers": answers, "elapsed_seconds": 10.0}
            ).json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            future_a = pool.submit(submit, all_correct)
            future_b = pool.submit(submit, all_wrong)
            result_a, result_b = future_a.result(), future_b.result()

    # Exactly one of the two should be the "winner" (already_completed False).
    already_completed_flags = sorted([result_a["already_completed"], result_b["already_completed"]])
    assert already_completed_flags == [False, True]

    # Both requests must report the SAME final stored score — no split-brain result.
    assert result_a["correct"] == result_b["correct"]

    row = supabase.table("scores").select("*").eq("user_id", REAL_TEST_USER_ID).eq(
        "date", date.today().isoformat()
    ).execute().data
    assert len(row) == 1  # UNIQUE(user_id, date) — never two rows


# --- Trivia review: unavailable before completion, available after --------------


def test_trivia_review_unavailable_before_completion():
    _auth_as()
    with TestClient(app) as client:
        response = client.get("/games/trivia-review")
    assert response.status_code == 404


def test_trivia_review_available_after_completion():
    _auth_as()
    with TestClient(app) as client:
        content = client.get("/games/daily-content").json()
        questions = content["trivia_questions"]
        answers = ["not a real option"] * len(questions)
        client.post("/games/submit-trivia", json={"answers": answers})

        response = client.get("/games/trivia-review")

    assert response.status_code == 200
    body = response.json()
    assert len(body["review"]) == len(questions)
    for item in body["review"]:
        assert "correct_answer" in item
        assert "selected_answer" in item
        assert item["is_correct"] is False  # we deliberately answered everything wrong


# --- Auth ---------------------------------------------------------------------


def test_unauthenticated_submissions_rejected():
    with TestClient(app) as client:
        assert client.post("/games/submit-maths", json={"answers": [], "elapsed_seconds": 5.0}).status_code in (401, 403)
        assert client.post("/games/submit-guess", json={"guess": "x"}).status_code in (401, 403)
        assert client.post("/games/submit-trivia", json={"answers": []}).status_code in (401, 403)


def _answer_for(problem: dict) -> int:
    left, right, operation = problem["left_operand"], problem["right_operand"], problem["operation"]
    if operation == "add":
        return left + right
    if operation == "subtract":
        return left - right
    if operation == "multiply":
        return left * right
    if operation == "divide":
        return left // right
    raise ValueError(f"unknown operation {operation!r}")
