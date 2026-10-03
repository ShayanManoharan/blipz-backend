"""Opt-in database tests for 20261003_atomic_game_completion.sql.

These tests create and fully remove isolated users/scores. They are deliberately
disabled by default so an unapplied review branch cannot touch the configured
database. After migration approval/application, run with:

    RUN_ATOMIC_COMPLETION_RPC_TESTS=1 pytest -q tests/test_atomic_completion_rpc.py
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.content_validation import compute_math_answer
from app.database import supabase
from app.main import app
from app.rate_limit import limiter
from app.routers.games import (
    _release_guess_reservation_as_failed,
    acquire_guess_scoring_slot,
    complete_game_attempt,
)
from app.scoring import compute_total_score


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_ATOMIC_COMPLETION_RPC_TESTS") != "1",
    reason="set RUN_ATOMIC_COMPLETION_RPC_TESTS=1 only after applying the reviewed RPC migration",
)


@pytest.fixture
def isolated_user():
    user_id = str(uuid.uuid4())
    username = f"atomic-{uuid.uuid4().hex[:16]}"
    supabase.table("users").insert({
        "id": user_id,
        "username": username,
        "current_streak": 0,
        "longest_streak": 0,
    }).execute()
    try:
        yield user_id
    finally:
        supabase.table("scores").delete().eq("user_id", user_id).execute()
        supabase.table("users").delete().eq("id", user_id).execute()


def _user(user_id):
    return supabase.table("users").select("current_streak,longest_streak").eq("id", user_id).single().execute().data


def _scores(user_id, for_date=None):
    query = supabase.table("scores").select("*").eq("user_id", user_id)
    if for_date:
        query = query.eq("date", for_date)
    return query.execute().data


def _complete(user_id, for_date, game, score=None):
    if game == "guess":
        return complete_game_attempt(user_id, for_date, game, 8.0 if score is None else score, {"guess_text": "test guess"})
    if game == "maths":
        return complete_game_attempt(
            user_id, for_date, game, 20 if score is None else score, {"maths_elapsed_seconds": 40.0}
        )
    return complete_game_attempt(
        user_id,
        for_date,
        game,
        4 if score is None else score,
        {"trivia_answers": [{"question_id": "q1", "selected_option_id": "A"}]},
    )


def test_guess_reservation_only_does_not_increment_streak(isolated_user):
    outcome, row = acquire_guess_scoring_slot(isolated_user, "2099-01-10")
    assert outcome == "acquired"
    assert row["guess_completed"] is False
    assert _user(isolated_user)["current_streak"] == 0


@pytest.mark.parametrize("game", ["guess", "maths", "trivia"])
def test_each_game_can_be_the_first_completion(isolated_user, game):
    if game == "guess":
        outcome, _ = acquire_guess_scoring_slot(isolated_user, "2099-01-11")
        assert outcome == "acquired"

    row, duplicate = _complete(isolated_user, "2099-01-11", game)

    assert duplicate is False
    assert row[f"{game}_completed"] is True
    assert _user(isolated_user) == {"current_streak": 1, "longest_streak": 1}


def test_failed_guess_reservation_does_not_increment_streak(isolated_user):
    outcome, row = acquire_guess_scoring_slot(isolated_user, "2099-01-12")
    assert outcome == "acquired"
    _release_guess_reservation_as_failed(row["id"])

    stored = _scores(isolated_user, "2099-01-12")[0]
    assert stored["guess_status"] == "failed"
    assert stored["guess_completed"] is False
    assert _user(isolated_user)["current_streak"] == 0


def test_duplicate_guess_returns_original_and_does_not_increment_again(isolated_user):
    acquire_guess_scoring_slot(isolated_user, "2099-01-13")
    first, first_duplicate = _complete(isolated_user, "2099-01-13", "guess", 8.0)
    second, second_duplicate = _complete(isolated_user, "2099-01-13", "guess", 1.0)

    assert first_duplicate is False
    assert second_duplicate is True
    assert second["guess_score"] == first["guess_score"] == 8.0
    assert second["guess_text"] == first["guess_text"] == "test guess"
    assert _user(isolated_user)["current_streak"] == 1


@pytest.mark.parametrize("second_game", ["maths", "trivia"])
def test_second_game_same_day_does_not_increment_again(isolated_user, second_game):
    acquire_guess_scoring_slot(isolated_user, "2099-01-14")
    _complete(isolated_user, "2099-01-14", "guess")
    row, duplicate = _complete(isolated_user, "2099-01-14", second_game)

    assert duplicate is False
    assert row[f"{second_game}_completed"] is True
    assert _user(isolated_user)["current_streak"] == 1


def test_concurrent_first_completions_increment_streak_exactly_once(isolated_user):
    with ThreadPoolExecutor(max_workers=2) as pool:
        maths = pool.submit(_complete, isolated_user, "2099-01-15", "maths")
        trivia = pool.submit(_complete, isolated_user, "2099-01-15", "trivia")
        maths_row, maths_duplicate = maths.result()
        trivia_row, trivia_duplicate = trivia.result()

    assert maths_duplicate is False
    assert trivia_duplicate is False
    assert maths_row["maths_completed"] is True
    assert trivia_row["trivia_completed"] is True
    final = _scores(isolated_user, "2099-01-15")[0]
    assert final["maths_completed"] is True and final["trivia_completed"] is True
    assert _user(isolated_user) == {"current_streak": 1, "longest_streak": 1}


def test_next_day_after_real_completion_increments_streak(isolated_user):
    _complete(isolated_user, "2099-01-16", "maths")
    _complete(isolated_user, "2099-01-17", "trivia")
    assert _user(isolated_user) == {"current_streak": 2, "longest_streak": 2}


def test_reservation_only_yesterday_resets_streak_to_one(isolated_user):
    supabase.table("users").update({"current_streak": 7, "longest_streak": 7}).eq("id", isolated_user).execute()
    acquire_guess_scoring_slot(isolated_user, "2099-01-18")
    _complete(isolated_user, "2099-01-19", "maths")
    assert _user(isolated_user) == {"current_streak": 1, "longest_streak": 7}


def test_gap_day_resets_current_streak(isolated_user):
    _complete(isolated_user, "2099-01-20", "maths")
    _complete(isolated_user, "2099-01-22", "trivia")
    assert _user(isolated_user) == {"current_streak": 1, "longest_streak": 1}


def test_longest_streak_survives_later_reset(isolated_user):
    _complete(isolated_user, "2099-01-23", "maths")
    _complete(isolated_user, "2099-01-24", "guess")
    _complete(isolated_user, "2099-01-26", "trivia")
    assert _user(isolated_user) == {"current_streak": 1, "longest_streak": 2}


def test_score_total_matches_existing_python_scoring_contract(isolated_user):
    _complete(isolated_user, "2099-01-27", "guess", 8.0)
    _complete(isolated_user, "2099-01-27", "trivia", 4)
    row, _ = _complete(isolated_user, "2099-01-27", "maths", 20)
    assert float(row["total_score"]) == compute_total_score(
        guess_score=8.0, trivia_correct=4, maths_elapsed_seconds=40.0
    )


@pytest.mark.parametrize("guess_score", [0.0, 2.5, 7.7, 10.0])
def test_guess_contribution_matches_python_scoring(isolated_user, guess_score):
    row, _ = _complete(isolated_user, "2099-02-01", "guess", guess_score)
    expected = compute_total_score(
        guess_score=guess_score,
        trivia_correct=0,
        maths_elapsed_seconds=None,
    )
    assert float(row["total_score"]) == expected
    assert float(row["total_score"]) == round(float(row["total_score"]), 1)


@pytest.mark.parametrize("trivia_score", [0, 1, 3, 5])
def test_trivia_contribution_matches_python_scoring(isolated_user, trivia_score):
    row, _ = _complete(isolated_user, "2099-02-02", "trivia", trivia_score)
    expected = compute_total_score(
        guess_score=0.0,
        trivia_correct=trivia_score,
        maths_elapsed_seconds=None,
    )
    assert float(row["total_score"]) == expected
    assert float(row["total_score"]) == round(float(row["total_score"]), 1)


MATHS_PARITY_CASES = [
    # Every interpolation boundary.
    25.0, 30.0, 40.0, 50.0, 60.0, 75.0, 90.0, 120.0, 150.0,
    # Representative points strictly between every pair of boundaries.
    27.5, 35.0, 45.0, 55.0, 67.5, 82.5, 105.0, 135.0,
    # Non-midpoint input that exercises one-decimal rounding.
    62.3,
]


@pytest.mark.parametrize("elapsed_seconds", MATHS_PARITY_CASES)
def test_maths_boundaries_interpolation_and_rounding_match_python(isolated_user, elapsed_seconds):
    # One isolated user is created per parameter case. The date is deliberately far
    # outside the product's competitive content window and requires no daily_content.
    row, _ = complete_game_attempt(
        isolated_user,
        "2099-02-03",
        "maths",
        20,
        {"maths_elapsed_seconds": elapsed_seconds},
    )
    expected = compute_total_score(
        guess_score=0.0,
        trivia_correct=0,
        maths_elapsed_seconds=elapsed_seconds,
    )
    assert float(row["total_score"]) == expected
    assert float(row["total_score"]) == round(float(row["total_score"]), 1)


def test_rpc_failure_rolls_back_score_and_streak_together(isolated_user):
    yesterday = "2099-01-27"
    today = "2099-01-28"
    _complete(isolated_user, yesterday, "maths")
    supabase.table("users").update({
        "current_streak": 2147483647,
        "longest_streak": 2147483647,
    }).eq("id", isolated_user).execute()

    with pytest.raises(Exception):
        _complete(isolated_user, today, "trivia")

    assert _scores(isolated_user, today) == []
    assert _user(isolated_user) == {
        "current_streak": 2147483647,
        "longest_streak": 2147483647,
    }


def test_fresh_guess_first_endpoint_e2e(isolated_daily_endpoint_env, monkeypatch):
    environment = isolated_daily_endpoint_env
    user_id = environment.user_id
    game_date = environment.date_string

    limiter.reset()
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    monkeypatch.setattr("app.routers.games.score_guess", lambda _guess, _prompt: _async_score(8.0))

    try:
        outcome, reservation = acquire_guess_scoring_slot(user_id, game_date)
        assert outcome == "acquired"
        assert reservation["guess_completed"] is False
        assert _user(user_id)["current_streak"] == 0
        _release_guess_reservation_as_failed(reservation["id"])

        with TestClient(app) as client:
            public_content = client.get("/games/daily-content")
            assert public_content.status_code == 200
            assert "image_prompt" not in public_content.json()

            guess = client.post("/games/submit-guess", json={"guess": "a blue robot beneath a tree"})
            assert guess.status_code == 200
            assert guess.json()["already_completed"] is False
            assert _user(user_id)["current_streak"] == 1

            duplicate_guess = client.post("/games/submit-guess", json={"guess": "a different retry"})
            assert duplicate_guess.status_code == 200
            assert duplicate_guess.json()["already_completed"] is True
            assert _user(user_id)["current_streak"] == 1

            math_answers = [
                compute_math_answer(p["left_operand"], p["right_operand"], p["operation"])
                for p in public_content.json()["math_problems"]
            ]
            maths = client.post(
                "/games/submit-maths",
                json={"answers": math_answers, "elapsed_seconds": 40.0},
            )
            assert maths.status_code == 200
            assert maths.json()["maths_score"] == 20
            assert _user(user_id)["current_streak"] == 1

            duplicate_maths = client.post(
                "/games/submit-maths",
                json={"answers": [answer + 1 for answer in math_answers], "elapsed_seconds": 80.0},
            )
            assert duplicate_maths.status_code == 200
            assert duplicate_maths.json()["already_completed"] is True
            assert duplicate_maths.json()["maths_score"] == 20
            assert _user(user_id)["current_streak"] == 1

            trivia_answers = [
                {"question_id": question["id"], "selected_option_id": question["correct_option_id"]}
                for question in environment.trivia_questions
            ]
            trivia = client.post("/games/submit-trivia", json={"answers": trivia_answers})
            assert trivia.status_code == 200
            assert trivia.json()["trivia_score"] == 5
            assert _user(user_id)["current_streak"] == 1

            duplicate_trivia = client.post(
                "/games/submit-trivia",
                json={
                    "answers": [
                        {"question_id": question["id"], "selected_option_id": "A"}
                        for question in environment.trivia_questions
                    ]
                },
            )
            assert duplicate_trivia.status_code == 200
            assert duplicate_trivia.json()["already_completed"] is True
            assert duplicate_trivia.json()["trivia_score"] == 5
            assert _user(user_id)["current_streak"] == 1

            me = client.get("/users/me")
            history = client.get("/users/me/history?days=5")
            leaderboard = client.get("/leaderboard/global")
            review = client.get("/games/guess-review")

        expected_total = compute_total_score(
            guess_score=8.0,
            trivia_correct=5,
            maths_elapsed_seconds=40.0,
        )
        assert expected_total == 87.0
        assert float(me.json()["total_score"]) == expected_total
        assert all([
            me.json()["guess_completed"],
            me.json()["maths_completed"],
            me.json()["trivia_completed"],
        ])

        history_row = next(row for row in history.json()["history"] if row["date"] == game_date)
        leaderboard_row = next(
            row for row in leaderboard.json()["leaderboard"] if row["username"] == environment.username
        )
        assert float(history_row["total_score"]) == expected_total
        assert float(leaderboard_row["total_score"]) == expected_total

        assert review.status_code == 200
        assert review.json()["actual_prompt"] == environment.image_prompt
        content_identity = supabase.table("daily_content").select(
            "package_revision_id,image_prompt"
        ).eq("id", environment.content_id).single().execute().data
        assert content_identity == {
            "package_revision_id": environment.package_revision_id,
            "image_prompt": review.json()["actual_prompt"],
        }
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
        limiter.reset()


async def _async_score(score: float) -> float:
    return score
