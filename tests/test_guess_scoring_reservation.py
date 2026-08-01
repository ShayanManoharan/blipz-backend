# Tests for PRODUCTION_AUDIT.md B23's fix: closing the Guess concurrency-cost window.
# complete_game_attempt's compare-and-swap already guaranteed only one score is ever
# *persisted*, but did nothing to stop two simultaneous first requests from both
# calling OpenAI before either wrote. acquire_guess_scoring_slot() (app/routers/games.py)
# adds an explicit, DB-backed reservation state machine (not_started -> scoring ->
# completed/failed) so at most one request can ever be scoring a given user/day's
# Guess attempt at a time.
#
# Requires sql/migrations.sql's guess_status/guess_scoring_started_at columns — these
# tests are skipped entirely if that migration hasn't been applied yet.

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.database import supabase
from app.main import app
from app.rate_limit import limiter
from app.routers.games import GUESS_SCORING_STALE_AFTER_SECONDS
from tests.conftest import requires_daily_content_status_migration

REAL_TEST_USER_ID = "d366ce2a-6cbc-48b9-881c-a4560c9dadf5"


def _migration_applied() -> bool:
    try:
        supabase.table("scores").select("guess_status").limit(1).execute()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(
        not _migration_applied(),
        reason="scores.guess_status not present — run sql/migrations.sql's latest block first",
    ),
    requires_daily_content_status_migration,
]


def _today() -> str:
    return date.today().isoformat()


def _cleanup():
    supabase.table("scores").delete().eq("user_id", REAL_TEST_USER_ID).eq("date", _today()).execute()


@pytest.fixture(autouse=True)
def _clean_before_and_after():
    _cleanup()
    limiter.reset()
    yield
    _cleanup()
    limiter.reset()
    app.dependency_overrides.pop(get_current_user_id, None)


def _auth():
    app.dependency_overrides[get_current_user_id] = lambda: REAL_TEST_USER_ID


def _row():
    return supabase.table("scores").select("*").eq("user_id", REAL_TEST_USER_ID).eq(
        "date", _today()
    ).execute().data[0]


# --- Normal path --------------------------------------------------------------------


@patch("app.routers.games.score_guess")
def test_normal_submission_calls_openai_exactly_once(mock_score_guess):
    mock_score_guess.return_value = 7.5
    _auth()
    with TestClient(app) as client:
        response = client.post("/games/submit-guess", json={"guess": "a spaceship"})

    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 7.5
    assert body["already_completed"] is False
    mock_score_guess.assert_called_once()

    row = _row()
    assert row["guess_completed"] is True
    assert row["guess_status"] == "completed"


@patch("app.routers.games.score_guess")
def test_zero_score_guess_completion_is_still_valid(mock_score_guess):
    mock_score_guess.return_value = 0.0
    _auth()
    with TestClient(app) as client:
        response = client.post("/games/submit-guess", json={"guess": "a completely wrong guess"})

    assert response.status_code == 200
    assert response.json()["score"] == 0.0

    row = _row()
    assert row["guess_completed"] is True
    assert row["guess_status"] == "completed"
    assert row["guess_score"] == 0.0


# --- Concurrency: at most one OpenAI call, ever --------------------------------------


@patch("app.routers.games.score_guess")
def test_two_concurrent_first_submissions_call_openai_exactly_once(mock_score_guess):
    async def fake_score(guess, prompt):
        import asyncio
        await asyncio.sleep(0.3)  # simulate real latency so both requests overlap
        return 7.0

    mock_score_guess.side_effect = fake_score
    _auth()
    with TestClient(app) as client:
        def submit(text):
            return client.post("/games/submit-guess", json={"guess": text})

        with ThreadPoolExecutor(max_workers=2) as pool:
            future_a = pool.submit(submit, "a spaceship")
            future_b = pool.submit(submit, "something totally different")
            response_a, response_b = future_a.result(), future_b.result()

    # The core guarantee: regardless of how the two responses ended up shaped, OpenAI
    # was only ever asked to score this attempt once.
    assert mock_score_guess.call_count == 1

    for response in (response_a, response_b):
        assert response.status_code in (200, 202)

    completed_scores = {r.json()["score"] for r in (response_a, response_b) if r.status_code == 200}
    assert completed_scores == {7.0}

    row = _row()
    assert row["guess_completed"] is True
    assert row["guess_score"] == 7.0


@patch("app.routers.games.score_guess")
def test_concurrent_caller_gets_in_progress_response_when_scoring_is_slow(mock_score_guess):
    async def slow_score(guess, prompt):
        import asyncio
        await asyncio.sleep(3.0)  # longer than the bounded wait (4 x 0.5s = 2s)
        return 6.0

    mock_score_guess.side_effect = slow_score
    _auth()
    with TestClient(app) as client:
        def submit(text):
            return client.post("/games/submit-guess", json={"guess": text})

        with ThreadPoolExecutor(max_workers=2) as pool:
            future_a = pool.submit(submit, "first")
            time.sleep(0.15)  # let the first request win the reservation before the second reads
            future_b = pool.submit(submit, "second")
            response_a, response_b = future_a.result(), future_b.result()

    assert mock_score_guess.call_count == 1
    assert response_a.status_code == 200
    assert response_b.status_code == 202
    body = response_b.json()
    assert body["status"] == "scoring_in_progress"


# --- Completed attempts never call OpenAI again --------------------------------------


@patch("app.routers.games.score_guess")
def test_completed_attempt_never_calls_openai_again(mock_score_guess):
    mock_score_guess.return_value = 8.0
    _auth()
    with TestClient(app) as client:
        first = client.post("/games/submit-guess", json={"guess": "first try"})
        second = client.post("/games/submit-guess", json={"guess": "a different guess entirely"})

    assert first.json()["already_completed"] is False
    assert second.json()["already_completed"] is True
    assert second.json()["score"] == first.json()["score"]
    mock_score_guess.assert_called_once()


# --- Failure recovery: not permanently stuck ------------------------------------------


@patch("app.routers.games.score_guess")
def test_openai_failure_releases_reservation_and_allows_retry(mock_score_guess):
    mock_score_guess.side_effect = [RuntimeError("simulated OpenAI outage"), 8.0]
    _auth()
    with TestClient(app) as client:
        first = client.post("/games/submit-guess", json={"guess": "a cat"})
        assert first.status_code == 502
        assert "OpenAI" not in first.json()["detail"]  # no internal exception details leaked

        row = _row()
        assert row["guess_status"] == "failed"
        assert row["guess_completed"] is False

        second = client.post("/games/submit-guess", json={"guess": "a cat, retried"})

    assert second.status_code == 200
    assert second.json()["already_completed"] is False
    assert mock_score_guess.call_count == 2

    row = _row()
    assert row["guess_completed"] is True
    assert row["guess_status"] == "completed"


# --- Stale reservation recovery (simulated process crash) ----------------------------


def test_stale_scoring_reservation_can_be_safely_reclaimed():
    stale_started_at = (
        datetime.now(timezone.utc) - timedelta(seconds=GUESS_SCORING_STALE_AFTER_SECONDS + 5)
    ).isoformat()
    supabase.table("scores").insert({
        "user_id": REAL_TEST_USER_ID,
        "date": _today(),
        "maths_score": 0, "trivia_score": 0, "guess_score": 0, "total_score": 0,
        "guess_status": "scoring",
        "guess_scoring_started_at": stale_started_at,
    }).execute()

    _auth()
    with patch("app.routers.games.score_guess", new=AsyncMock(return_value=9.0)) as mock_score_guess:
        with TestClient(app) as client:
            response = client.post("/games/submit-guess", json={"guess": "reclaimed after crash"})

    assert response.status_code == 200
    assert response.json()["already_completed"] is False
    assert response.json()["score"] == 9.0
    mock_score_guess.assert_called_once()

    row = _row()
    assert row["guess_completed"] is True
    assert row["guess_status"] == "completed"


def test_fresh_scoring_reservation_is_not_treated_as_stale():
    # A reservation started just now (not stale) must NOT be reclaimable — this is what
    # makes the in-progress path safe in the first place.
    supabase.table("scores").insert({
        "user_id": REAL_TEST_USER_ID,
        "date": _today(),
        "maths_score": 0, "trivia_score": 0, "guess_score": 0, "total_score": 0,
        "guess_status": "scoring",
        "guess_scoring_started_at": datetime.now(timezone.utc).isoformat(),
    }).execute()

    _auth()
    with patch("app.routers.games.score_guess", new=AsyncMock(return_value=9.0)) as mock_score_guess:
        with TestClient(app) as client:
            response = client.post("/games/submit-guess", json={"guess": "should not score"})

    # Bounded wait expires (the fake reservation never completes) -> 202, never OpenAI.
    assert response.status_code == 202
    mock_score_guess.assert_not_called()


# --- Validation / auth / rate limiting unchanged --------------------------------------


def test_unauthenticated_guess_submission_rejected():
    with TestClient(app) as client:
        response = client.post("/games/submit-guess", json={"guess": "x"})
    assert response.status_code in (401, 403)


def test_empty_guess_rejected():
    _auth()
    with TestClient(app) as client:
        response = client.post("/games/submit-guess", json={"guess": ""})
    assert response.status_code == 422
