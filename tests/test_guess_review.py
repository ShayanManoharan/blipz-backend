# Regression tests for the post-completion Guess review flow (GET /games/guess-review).
# image_prompt must stay absent from GET /games/daily-content forever (PRODUCTION_AUDIT.md
# B1) — guess-review is a *second*, narrower door that only opens once guess_completed is
# already true for that user/day, at which point the attempt is permanently locked and
# there is nothing left to leak the prompt to influence.

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.database import supabase
from app.main import app
from tests.conftest import requires_daily_content_status_migration
from tests.test_daily_content_security import _migration_applied, _guess_status_migration_applied

REAL_TEST_USER_ID = "d366ce2a-6cbc-48b9-881c-a4560c9dadf5"

requires_migration = pytest.mark.skipif(
    not _migration_applied(),
    reason="scores.maths_completed etc. not present — run sql/migrations.sql's latest block first",
)
requires_guess_status_migration = pytest.mark.skipif(
    not _guess_status_migration_applied(),
    reason="scores.guess_status not present — run sql/migrations.sql's latest block first",
)


def _cleanup_scores_row(user_id: str):
    today = date.today().isoformat()
    supabase.table("scores").delete().eq("user_id", user_id).eq("date", today).execute()


def _override_auth(user_id=REAL_TEST_USER_ID):
    app.dependency_overrides[get_current_user_id] = lambda: user_id


def _clear_auth_override():
    app.dependency_overrides.pop(get_current_user_id, None)


def test_guess_review_requires_authentication():
    with TestClient(app) as client:
        response = client.get("/games/guess-review")
    assert response.status_code in (401, 403)


@requires_daily_content_status_migration
def test_daily_content_still_excludes_image_prompt():
    _override_auth()
    try:
        with TestClient(app) as client:
            response = client.get("/games/daily-content")
    finally:
        _clear_auth_override()

    if response.status_code == 404:
        return  # no content seeded for today in this environment

    assert "image_prompt" not in response.json()


@requires_migration
@requires_guess_status_migration
@requires_daily_content_status_migration
def test_guess_review_404s_before_guess_completed():
    _cleanup_scores_row(REAL_TEST_USER_ID)
    _override_auth()
    try:
        with TestClient(app) as client:
            response = client.get("/games/guess-review")
    finally:
        _clear_auth_override()
        _cleanup_scores_row(REAL_TEST_USER_ID)

    assert response.status_code == 404


@requires_migration
@requires_guess_status_migration
@requires_daily_content_status_migration
@patch("app.routers.games.score_guess")
def test_guess_review_returns_prompt_and_guess_after_completion(mock_score_guess):
    mock_score_guess.return_value = 7.5

    _cleanup_scores_row(REAL_TEST_USER_ID)
    _override_auth()
    try:
        with TestClient(app) as client:
            submit_response = client.post("/games/submit-guess", json={"guess": "a raccoon in a chef hat"})
            if submit_response.status_code == 404:
                return  # no daily content seeded in this environment

            review_response = client.get("/games/guess-review")
    finally:
        _clear_auth_override()
        _cleanup_scores_row(REAL_TEST_USER_ID)

    assert submit_response.status_code == 200
    assert review_response.status_code == 200
    body = review_response.json()
    assert body["guess"] == "a raccoon in a chef hat"
    assert body["score"] == 7.5
    assert body["actual_prompt"] == submit_response.json()["actual_prompt"]
    assert body["actual_prompt"]  # non-empty — this IS the real generation prompt


@requires_migration
@requires_guess_status_migration
@requires_daily_content_status_migration
@patch("app.routers.games.score_guess")
def test_repeated_guess_submission_stays_locked_to_first_result(mock_score_guess):
    mock_score_guess.return_value = 4.0

    _cleanup_scores_row(REAL_TEST_USER_ID)
    _override_auth()
    try:
        with TestClient(app) as client:
            first = client.post("/games/submit-guess", json={"guess": "first guess"})
            if first.status_code == 404:
                return  # no daily content seeded in this environment

            mock_score_guess.return_value = 9.9  # would be the score IF a second call scored
            second = client.post("/games/submit-guess", json={"guess": "a completely different second guess"})
            review = client.get("/games/guess-review")
    finally:
        _clear_auth_override()
        _cleanup_scores_row(REAL_TEST_USER_ID)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["already_completed"] is True
    # The second call must not have re-scored — score and guess stay pinned to the first.
    assert second.json()["guess"] == "first guess"
    assert second.json()["score"] == first.json()["score"]
    assert review.json()["guess"] == "first guess"
    assert review.json()["score"] == first.json()["score"]
