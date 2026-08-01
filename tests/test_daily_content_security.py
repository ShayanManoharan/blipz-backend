# Regression tests for PRODUCTION_AUDIT.md finding B1: GET /games/daily-content used to
# be unauthenticated and leaked answer fields (math/trivia) and the Guess image_prompt
# before players had a chance to play. These tests pin down the fixed behavior.

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.database import supabase
from app.main import app
from tests.conftest import requires_daily_content_status_migration

# This project has no test-DB isolation — the existing suite already exercises the
# real configured Supabase project (see test_main.py). Submitting a score requires a
# user_id that satisfies the real `scores.user_id` FK, so these tests use a real seeded
# user and clean up the row they create/modify afterward rather than a fake UUID.
TEST_USER_ID = "00000000-0000-0000-0000-000000000000"
REAL_TEST_USER_ID = "d366ce2a-6cbc-48b9-881c-a4560c9dadf5"


def _migration_applied() -> bool:
    try:
        supabase.table("scores").select("maths_completed").limit(1).execute()
        return True
    except Exception:
        return False


# The two submit-* tests below now go through complete_game_attempt() (see B2's
# follow-up fix), which writes guess_text/trivia_answers columns that only exist once
# sql/migrations.sql's latest block has been applied.
requires_migration = pytest.mark.skipif(
    not _migration_applied(),
    reason="scores.maths_completed etc. not present — run sql/migrations.sql's latest block first",
)


def _guess_status_migration_applied() -> bool:
    try:
        supabase.table("scores").select("guess_status").limit(1).execute()
        return True
    except Exception:
        return False


# submit_guess unconditionally reads/writes guess_status/guess_scoring_started_at now
# (see PRODUCTION_AUDIT.md B23's fix).
requires_guess_status_migration = pytest.mark.skipif(
    not _guess_status_migration_applied(),
    reason="scores.guess_status not present — run sql/migrations.sql's latest block first",
)


def _cleanup_scores_row(user_id: str):
    today = date.today().isoformat()
    supabase.table("scores").delete().eq("user_id", user_id).eq("date", today).execute()


# Fields that must never appear anywhere in the public daily-content payload.
FORBIDDEN_TOP_LEVEL_FIELDS = {"prompt", "image_prompt", "scoring_reference", "rubric", "target_concepts"}
FORBIDDEN_TRIVIA_FIELDS = {
    "answer", "correct_answer", "correct_option_id", "correct_index", "explanation",
    "scoring_reference", "rubric",
}


def _override_auth():
    app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID


def _clear_auth_override():
    app.dependency_overrides.pop(get_current_user_id, None)


def test_daily_content_requires_authentication():
    with TestClient(app) as client:
        response = client.get("/games/daily-content")
    assert response.status_code in (401, 403)


@requires_daily_content_status_migration
def test_daily_content_authenticated_anonymous_user_can_fetch():
    _override_auth()
    try:
        with TestClient(app) as client:
            response = client.get("/games/daily-content")
    finally:
        _clear_auth_override()

    # 404 only means no content has been generated for today in this environment —
    # not an auth failure. Anything else means the auth-gate change broke normal access.
    assert response.status_code in (200, 404)


@requires_daily_content_status_migration
def test_daily_content_does_not_leak_forbidden_fields():
    _override_auth()
    try:
        with TestClient(app) as client:
            response = client.get("/games/daily-content")
    finally:
        _clear_auth_override()

    if response.status_code == 404:
        return  # no content seeded for today in this environment — nothing to check

    body = response.json()

    for field in FORBIDDEN_TOP_LEVEL_FIELDS:
        assert field not in body, f"forbidden field '{field}' present at top level"

    for question in body.get("trivia_questions", []):
        for field in FORBIDDEN_TRIVIA_FIELDS:
            assert field not in question, f"forbidden field '{field}' present on a trivia question"

    # math_problems[].answer is a documented, intentional exception (see
    # PublicMathProblem in app/models/schemas.py) — deliberately not asserted absent.


@requires_daily_content_status_migration
def test_daily_content_shape_matches_ios_decodable_model():
    # Pinned exactly to Blipz/Models/DailyContent.swift's Decodable structs. If this
    # test needs updating, the iOS model needs the matching update too.
    _override_auth()
    try:
        with TestClient(app) as client:
            response = client.get("/games/daily-content")
    finally:
        _clear_auth_override()

    if response.status_code == 404:
        return

    body = response.json()
    assert set(body.keys()) == {"id", "date", "image_url", "math_problems", "trivia_questions"}
    for problem in body["math_problems"]:
        assert set(problem.keys()) == {"left_operand", "right_operand", "operation"}
    for question in body["trivia_questions"]:
        assert set(question.keys()) == {"id", "question", "category", "options"}


@requires_migration
@requires_guess_status_migration
@requires_daily_content_status_migration
@patch("app.routers.games.score_guess")
def test_submit_guess_scores_via_server_fetched_prompt_not_client_supplied(mock_score_guess):
    mock_score_guess.return_value = 5.0

    _cleanup_scores_row(REAL_TEST_USER_ID)
    app.dependency_overrides[get_current_user_id] = lambda: REAL_TEST_USER_ID
    try:
        with TestClient(app) as client:
            response = client.post(
                "/games/submit-guess",
                json={"guess": "a test guess", "image_prompt": "a fake client-supplied prompt"},
            )
    finally:
        _clear_auth_override()
        _cleanup_scores_row(REAL_TEST_USER_ID)

    if response.status_code == 404:
        return  # no daily content seeded in this environment

    assert response.status_code == 200
    mock_score_guess.assert_called_once()
    called_guess, called_prompt = mock_score_guess.call_args.args
    assert called_guess == "a test guess"
    # The endpoint must have used its own DB-fetched prompt, never the client's.
    assert called_prompt != "a fake client-supplied prompt"


@requires_migration
@requires_daily_content_status_migration
def test_submit_trivia_has_no_client_supplied_correctness_field():
    # TriviaAnswerSubmit only ever accepts question_id + selected_option_id — there is
    # no field for a client to assert its own correctness/score, so grading is
    # structurally always server-side. This test also confirms the endpoint still works
    # after the id-based grading fix (see PRODUCTION_AUDIT.md's Trivia grading fix).
    _cleanup_scores_row(REAL_TEST_USER_ID)
    app.dependency_overrides[get_current_user_id] = lambda: REAL_TEST_USER_ID
    try:
        with TestClient(app) as client:
            content = client.get("/games/daily-content")
            if content.status_code == 404:
                return
            answers = [{"question_id": q["id"], "selected_option_id": "A"} for q in content.json()["trivia_questions"]]
            response = client.post("/games/submit-trivia", json={"answers": answers})
    finally:
        _clear_auth_override()
        _cleanup_scores_row(REAL_TEST_USER_ID)

    assert response.status_code == 200
    body = response.json()
    assert 0 <= body["correct"] <= body["total"]
