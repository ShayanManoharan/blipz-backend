# Unit tests for the normalized 100-point scoring formula (Guess 40 / Trivia 30 /
# Maths 30) and its date-based legacy/normalized cutover. See app/scoring.py's module
# docstring for why the cutover is date-based rather than a migration/flag.

import pytest

from app.scoring import (
    MATHS_BREAKPOINTS,
    SCORING_V2_CUTOVER_DATE,
    compute_guess_points,
    compute_legacy_total_score,
    compute_maths_points,
    compute_row_total,
    compute_total_score,
    compute_trivia_points,
    uses_normalized_scoring,
)


# --- compute_maths_points: every named breakpoint --------------------------------

@pytest.mark.parametrize("elapsed,expected_points", MATHS_BREAKPOINTS)
def test_maths_points_at_every_breakpoint(elapsed, expected_points):
    assert compute_maths_points(elapsed) == expected_points


# --- compute_maths_points: continuous interpolation between breakpoints ----------

@pytest.mark.parametrize(
    "elapsed,expected_points",
    [
        (27.5, 29.0),   # midpoint of (25, 30) and (30, 28)
        (35, 26.5),     # midpoint of (30, 28) and (40, 25)
        (45, 23.5),     # midpoint of (40, 25) and (50, 22)
        (62, 18.466666666666665),  # the user's worked example, between (60, 19) and (75, 15)
        (82.5, 13.0),   # midpoint of (75, 15) and (90, 11)
        (105, 8.5),     # midpoint of (90, 11) and (120, 6)
        (135, 4.5),     # midpoint of (120, 6) and (150, 3)
    ],
)
def test_maths_points_between_breakpoints(elapsed, expected_points):
    assert compute_maths_points(elapsed) == pytest.approx(expected_points)


# --- compute_maths_points: cap below the fastest breakpoint, floor above the slowest --

@pytest.mark.parametrize("elapsed", [0, 1, 10, 24.9, 25])
def test_maths_points_capped_at_or_under_25_seconds(elapsed):
    assert compute_maths_points(elapsed) == 30


@pytest.mark.parametrize("elapsed", [150, 151, 300, 10_000])
def test_maths_points_floored_at_or_over_150_seconds(elapsed):
    assert compute_maths_points(elapsed) == 3


def test_maths_points_none_means_not_completed_yet():
    assert compute_maths_points(None) == 0.0


# --- compute_guess_points / compute_trivia_points --------------------------------

@pytest.mark.parametrize("guess_score,expected", [(0, 0.0), (5.0, 20.0), (8.0, 32.0), (10.0, 40.0)])
def test_guess_points(guess_score, expected):
    assert compute_guess_points(guess_score) == expected


@pytest.mark.parametrize("trivia_correct,expected", [(0, 0.0), (1, 6.0), (3, 18.0), (5, 30.0)])
def test_trivia_points(trivia_correct, expected):
    assert compute_trivia_points(trivia_correct) == expected


# --- compute_total_score: full worked examples -----------------------------------

def test_total_score_matches_the_reported_example_exactly():
    # Guess 8.0/10, Maths 62s, Trivia 3/5 — the example from the "31.0 is confusing"
    # report this scoring model replaces.
    total = compute_total_score(guess_score=8.0, trivia_correct=3, maths_elapsed_seconds=62)
    assert total == 68.5


@pytest.mark.parametrize(
    "guess_score,trivia_correct,maths_elapsed_seconds,expected_total",
    [
        (10.0, 5, 25, 100.0),   # perfect everything, instant-cap maths
        (0.0, 0, None, 0.0),    # nothing completed yet
        (0.0, 0, 200, 3.0),     # only a very slow Maths run completed
        (10.0, 0, None, 40.0),  # only a perfect Guess completed
        (0.0, 5, None, 30.0),   # only a perfect Trivia completed
    ],
)
def test_total_score_full_examples(guess_score, trivia_correct, maths_elapsed_seconds, expected_total):
    assert compute_total_score(
        guess_score=guess_score, trivia_correct=trivia_correct, maths_elapsed_seconds=maths_elapsed_seconds
    ) == expected_total


# --- Legacy formula: preserved exactly for pre-cutover dates ---------------------

def test_legacy_total_score_is_unweighted_raw_sum():
    assert compute_legacy_total_score(maths_correct=20, trivia_correct=3, guess_score=8.0) == 31.0


# --- Cutover: date-based, never a stored flag ------------------------------------

def test_uses_normalized_scoring_is_false_strictly_before_cutover():
    from datetime import timedelta

    assert uses_normalized_scoring(SCORING_V2_CUTOVER_DATE - timedelta(days=1)) is False


def test_uses_normalized_scoring_is_true_on_and_after_cutover():
    from datetime import timedelta

    assert uses_normalized_scoring(SCORING_V2_CUTOVER_DATE) is True
    assert uses_normalized_scoring(SCORING_V2_CUTOVER_DATE + timedelta(days=1)) is True


def test_compute_row_total_picks_legacy_formula_before_cutover():
    from datetime import timedelta

    pre_cutover = (SCORING_V2_CUTOVER_DATE - timedelta(days=1)).isoformat()
    total = compute_row_total(
        pre_cutover, maths_correct=20, trivia_correct=3, guess_score=8.0, maths_elapsed_seconds=None
    )
    # Same 31.0 as the legacy formula — elapsed_seconds is irrelevant pre-cutover, and
    # deliberately not even available for real historical rows (see app/scoring.py).
    assert total == 31.0


def test_compute_row_total_picks_normalized_formula_on_and_after_cutover():
    on_cutover = SCORING_V2_CUTOVER_DATE.isoformat()
    total = compute_row_total(
        on_cutover, maths_correct=20, trivia_correct=3, guess_score=8.0, maths_elapsed_seconds=62
    )
    assert total == 68.5


# --- End-to-end: the real endpoints produce the normalized total for today ------
# The unit tests above pin the pure formula; this confirms complete_game_attempt
# actually wires it in through the real /games/submit-* routes for a real (today,
# post-cutover) date, not just in isolation.

from datetime import date
from unittest.mock import patch

import pytest as _pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.database import supabase
from app.main import app
from tests.conftest import requires_daily_content_status_migration
from tests.test_daily_content_security import _migration_applied

REAL_TEST_USER_ID = "d366ce2a-6cbc-48b9-881c-a4560c9dadf5"

requires_migration = _pytest.mark.skipif(
    not _migration_applied(), reason="scores.maths_completed etc. not present — run sql/migrations.sql's latest block first"
)


def _cleanup_scores_row(user_id: str):
    today = date.today().isoformat()
    supabase.table("scores").delete().eq("user_id", user_id).eq("date", today).execute()


@requires_migration
@requires_daily_content_status_migration
@patch("app.routers.games.score_guess")
def test_submit_endpoints_produce_normalized_total_for_todays_date(mock_score_guess):
    assert uses_normalized_scoring(date.today()), "this test assumes today is on/after the v2 cutover"
    mock_score_guess.return_value = 8.0

    _cleanup_scores_row(REAL_TEST_USER_ID)
    app.dependency_overrides[get_current_user_id] = lambda: REAL_TEST_USER_ID
    try:
        with TestClient(app) as client:
            content = client.get("/games/daily-content")
            if content.status_code == 404:
                return  # no daily content seeded in this environment

            problems = content.json()["math_problems"]
            answers = [
                p["left_operand"] + p["right_operand"] if p["operation"] == "add"
                else p["left_operand"] - p["right_operand"] if p["operation"] == "subtract"
                else p["left_operand"] * p["right_operand"] if p["operation"] == "multiply"
                else p["left_operand"] // p["right_operand"]
                for p in problems
            ]
            maths_response = client.post(
                "/games/submit-maths", json={"answers": answers, "elapsed_seconds": 62}
            )

            trivia_questions = content.json()["trivia_questions"]
            # Answer only the first 3 correctly is impossible without knowing the correct
            # option — instead submit all "A" and read back however many that happens to
            # score, then assert the total reflects THAT actual trivia_score exactly.
            trivia_answers = [{"question_id": q["id"], "selected_option_id": "A"} for q in trivia_questions]
            trivia_response = client.post("/games/submit-trivia", json={"answers": trivia_answers})

            guess_response = client.post("/games/submit-guess", json={"guess": "a test guess"})

            me_response = client.get("/users/me")
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
        _cleanup_scores_row(REAL_TEST_USER_ID)

    assert maths_response.status_code == 200
    assert trivia_response.status_code == 200
    assert guess_response.status_code == 200

    expected_total = compute_total_score(
        guess_score=guess_response.json()["score"],
        trivia_correct=trivia_response.json()["trivia_score"],
        maths_elapsed_seconds=62,
    )
    assert me_response.json()["total_score"] == expected_total
    # Sanity: the endpoint's total is NOT the old raw-count sum — guards against
    # silently falling back to legacy for a post-cutover date. With a real 20/20 maths
    # run, mocked guess=8.0, and trivia_correct in [0, 5], the two formulas can never
    # coincide (normalized ~= 50.5 + 6*trivia_correct vs. legacy = 28 + trivia_correct).
    legacy_total = compute_legacy_total_score(
        maths_correct=maths_response.json()["maths_score"],
        trivia_correct=trivia_response.json()["trivia_score"],
        guess_score=guess_response.json()["score"],
    )
    assert me_response.json()["total_score"] != legacy_total
