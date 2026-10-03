from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.routers import games


MIGRATION = Path(__file__).parents[1] / "sql" / "20261003_atomic_game_completion.sql"


def test_migration_security_and_locking_contract():
    sql = MIGRATION.read_text()

    assert "CREATE OR REPLACE FUNCTION public.complete_game_attempt_atomic(" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog" in sql
    assert "FROM public.users AS u" in sql
    assert "FROM public.scores AS s" in sql
    assert sql.count("FOR UPDATE;") >= 2
    assert "ON CONFLICT (user_id, date) DO NOTHING" in sql

    for role in ("PUBLIC", "anon", "authenticated"):
        assert f") FROM {role};" in sql
    assert ") TO service_role;" in sql


def test_migration_streak_and_idempotency_contract():
    sql = MIGRATION.read_text()

    assert "RETURN QUERY SELECT pg_catalog.to_jsonb(v_score), TRUE, FALSE" in sql
    assert "v_first_completion := NOT" in sql
    assert "yesterday.maths_completed" in sql
    assert "yesterday.guess_completed" in sql
    assert "yesterday.trivia_completed" in sql
    assert "WHEN v_played_yesterday THEN COALESCE(v_user.current_streak, 0) + 1" in sql
    assert "ELSE 1" in sql
    assert "longest_streak = GREATEST(" in sql

    # This migration defines a function and privileges only; it does not rewrite data.
    assert "UPDATE public.daily_content" not in sql
    assert "UPDATE public.scores AS s" in sql
    assert "WHERE s.id = v_score.id" in sql
    assert "UPDATE public.users AS u" in sql
    assert "WHERE u.id = p_user_id" in sql


def test_complete_game_attempt_calls_atomic_rpc_with_game_metadata():
    stored_row = {
        "user_id": "00000000-0000-0000-0000-000000000001",
        "date": "2099-01-01",
        "maths_score": 20,
        "maths_completed": True,
        "total_score": 25.0,
    }
    response = SimpleNamespace(data=[{
        "score_row": stored_row,
        "already_completed": False,
        "streak_updated": True,
    }])

    with patch.object(games.supabase, "rpc") as rpc:
        rpc.return_value.execute.return_value = response
        row, already_completed = games.complete_game_attempt(
            stored_row["user_id"],
            stored_row["date"],
            "maths",
            20,
            {"maths_elapsed_seconds": 40.0},
        )

    assert row == stored_row
    assert already_completed is False
    rpc.assert_called_once_with(
        "complete_game_attempt_atomic",
        {
            "p_user_id": stored_row["user_id"],
            "p_date": stored_row["date"],
            "p_game": "maths",
            "p_score": 20,
            "p_maths_elapsed_seconds": 40.0,
            "p_guess_text": None,
            "p_trivia_answers": None,
        },
    )


def test_complete_game_attempt_preserves_duplicate_result():
    stored_row = {"guess_score": 8.5, "guess_text": "original", "guess_completed": True}
    response = SimpleNamespace(data=[{
        "score_row": stored_row,
        "already_completed": True,
        "streak_updated": False,
    }])

    with patch.object(games.supabase, "rpc") as rpc:
        rpc.return_value.execute.return_value = response
        row, already_completed = games.complete_game_attempt(
            "00000000-0000-0000-0000-000000000001",
            "2099-01-01",
            "guess",
            2.0,
            {"guess_text": "retry"},
        )

    assert row == stored_row
    assert already_completed is True


def test_application_has_no_competing_update_streak_function():
    assert not hasattr(games, "update_streak")
