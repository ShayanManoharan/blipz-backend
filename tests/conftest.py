# conftest.py
# Shared migration-gating helpers. The generate/publish pipeline (see
# PRODUCTION_AUDIT.md's deployment plan) added daily_content.status, which
# GET /games/daily-content and every /games/submit-* endpoint now filters on — so
# virtually every test that touches a games endpoint depends on it, across several
# test files. Centralized here instead of duplicating the same check per file.

from dataclasses import dataclass
from datetime import date
from uuid import uuid4

import pytest

from app.content_validation import (
    CONTENT_SCHEMA_VERSION,
    GENERATOR_VERSION,
    validate_daily_package,
)
from app.database import supabase
from app.routers import games, leaderboard, users


def daily_content_status_migration_applied() -> bool:
    try:
        supabase.table("daily_content").select("status").limit(1).execute()
        return True
    except Exception:
        return False


requires_daily_content_status_migration = pytest.mark.skipif(
    not daily_content_status_migration_applied(),
    reason="daily_content.status not present — run sql/migrations.sql's latest block first",
)


ISOLATED_GAME_DATE = date(2099, 12, 30)


@dataclass(frozen=True)
class IsolatedDailyEndpointEnvironment:
    date: date
    user_id: str
    username: str
    content_id: str
    package_revision_id: str
    image_prompt: str
    math_problems: list[dict]
    trivia_questions: list[dict]

    @property
    def date_string(self) -> str:
        return self.date.isoformat()


def _deterministic_math_problems() -> list[dict]:
    problems = []
    for index in range(5):
        problems.extend([
            {"left_operand": 10 + index, "right_operand": 2 + index, "operation": "add"},
            {"left_operand": 20 + index, "right_operand": 3 + index, "operation": "subtract"},
            {"left_operand": 3 + index, "right_operand": 2, "operation": "multiply"},
            {"left_operand": (4 + index) * 2, "right_operand": 2, "operation": "divide"},
        ])
    return problems


def _deterministic_trivia_questions() -> list[dict]:
    correct_ids = ["A", "B", "C", "D", "A"]
    return [
        {
            "id": f"q{index}",
            "question": f"Isolated test question {index + 1}?",
            "category": "Test",
            "options": [
                f"Question {index + 1} option A",
                f"Question {index + 1} option B",
                f"Question {index + 1} option C",
                f"Question {index + 1} option D",
            ],
            "correct_option_id": correct_ids[index],
        }
        for index in range(5)
    ]


@pytest.fixture
def isolated_daily_endpoint_env(monkeypatch, request) -> IsolatedDailyEndpointEnvironment:
    """Create a complete disposable daily package and user for endpoint tests.

    The fixed date is intentionally outside the product's competitive window. A
    collision is treated as a hard safety failure; the fixture never overwrites an
    existing row. Every delete is constrained by the exact UUID/date it created.
    """

    test_date = ISOLATED_GAME_DATE
    date_string = test_date.isoformat()
    existing_content = supabase.table("daily_content").select("id").eq("date", date_string).execute().data
    assert existing_content == [], f"refusing to overwrite existing daily_content for {date_string}"

    user_id = str(uuid4())
    username = f"endpoint-fixture-{uuid4().hex[:16]}"
    content_id = str(uuid4())
    revision_id = str(uuid4())
    storage_key = f"test-fixtures/{date_string}/{revision_id}.png"
    timestamp = "2099-12-30T00:00:00+00:00"
    image_prompt = "A cobalt robot reading beneath a glowing willow tree"
    math_problems = _deterministic_math_problems()
    trivia_questions = _deterministic_trivia_questions()

    package = {
        "content_schema_version": CONTENT_SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "package_revision_id": revision_id,
        "original_image_prompt": image_prompt,
        "effective_image_prompt": None,
        "image_prompt": image_prompt,
        "image_storage_key": storage_key,
        "image_url": f"https://example.invalid/storage/v1/object/public/blipz-images/{storage_key}",
        "image_sha256": "0" * 64,
        "image_model": "test-fixture-model",
        "image_generated_at": timestamp,
        "image_verified_at": timestamp,
        "math_problems": math_problems,
        "trivia_questions": trivia_questions,
    }
    validate_daily_package(package)

    content_row = {
        "id": content_id,
        "date": date_string,
        **package,
        "status": "published",
        "generated_at": timestamp,
        "published_at": timestamp,
        "validated_at": timestamp,
        "validation_result": {"valid": True, "validator": "isolated-test-fixture"},
        "is_fallback": False,
        "daily_message": "isolated test day",
    }

    supabase.table("users").insert({
        "id": user_id,
        "username": username,
        "current_streak": 0,
        "longest_streak": 0,
    }).execute()
    try:
        supabase.table("daily_content").insert(content_row).execute()
    except Exception:
        supabase.table("users").delete().eq("id", user_id).execute()
        raise

    class FixedTestDate(date):
        @classmethod
        def today(cls):
            return test_date

    monkeypatch.setattr(games, "utc_today", lambda: test_date)
    monkeypatch.setattr(users, "date", FixedTestDate)
    monkeypatch.setattr(leaderboard, "date", FixedTestDate)

    # Existing endpoint test modules centralize their shared IDs/dates behind these
    # names. Patching them keeps their assertions intact while removing all dependence
    # on a real seeded user or the wall clock.
    test_module = request.module
    if hasattr(test_module, "date"):
        monkeypatch.setattr(test_module, "date", FixedTestDate)
    for user_constant in ("REAL_TEST_USER_ID", "TEST_USER_ID"):
        if hasattr(test_module, user_constant):
            monkeypatch.setattr(test_module, user_constant, user_id)

    environment = IsolatedDailyEndpointEnvironment(
        date=test_date,
        user_id=user_id,
        username=username,
        content_id=content_id,
        package_revision_id=revision_id,
        image_prompt=image_prompt,
        math_problems=math_problems,
        trivia_questions=trivia_questions,
    )

    try:
        yield environment
    finally:
        supabase.table("scores").delete().eq("user_id", user_id).eq("date", date_string).execute()
        supabase.table("daily_content").delete().eq("id", content_id).eq("date", date_string).execute()
        supabase.table("users").delete().eq("id", user_id).execute()

        assert supabase.table("scores").select("id").eq("user_id", user_id).execute().data == []
        assert supabase.table("daily_content").select("id").eq("id", content_id).execute().data == []
        assert supabase.table("users").select("id").eq("id", user_id).execute().data == []
