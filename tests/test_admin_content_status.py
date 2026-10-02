"""Focused coverage for safe, date-specific admin content diagnostics."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.agents import content_generator as cg
from app.config import settings
from app.database import supabase
from app.main import app
from tests.conftest import requires_daily_content_status_migration
from tests.test_content_pipeline import _mocked_generation


TEST_DATE = date(2099, 7, 2)
REAL_TEST_USER_ID = "d366ce2a-6cbc-48b9-881c-a4560c9dadf5"
ORIGINAL_PROMPT = "A hidden original observability prompt"
REVISED_PROMPT = "A hidden revised observability prompt"


def _cleanup():
    date_str = TEST_DATE.isoformat()
    supabase.table("daily_content_replacement_log").delete().eq(
        "content_date", date_str
    ).execute()
    supabase.table("daily_content_generation_log").delete().eq(
        "content_date", date_str
    ).execute()
    supabase.table("scores").delete().eq("date", date_str).execute()
    supabase.table("daily_content").delete().eq("date", date_str).execute()


@pytest.fixture(autouse=True)
def _isolated_diagnostics_date():
    _cleanup()
    yield
    _cleanup()


def _admin_get(content_date: str):
    with TestClient(app) as client:
        return client.get(
            "/admin/content-status",
            params={"content_date": content_date},
            headers={"x-admin-token": settings.admin_token},
        )


def _all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_keys(child)


def test_content_status_remains_admin_protected():
    with TestClient(app) as client:
        missing = client.get(
            "/admin/content-status", params={"content_date": TEST_DATE.isoformat()}
        )
        invalid = client.get(
            "/admin/content-status",
            params={"content_date": TEST_DATE.isoformat()},
            headers={"x-admin-token": "definitely-wrong"},
        )

    assert missing.status_code in (401, 422)
    assert invalid.status_code == 401


def test_content_status_rejects_invalid_requested_date():
    response = _admin_get("not-a-date")
    assert response.status_code == 400
    assert response.json()["detail"] == "content_date must be YYYY-MM-DD"


@requires_daily_content_status_migration
def test_content_status_preserves_existing_no_date_dashboard_contract():
    with TestClient(app) as client:
        response = client.get(
            "/admin/content-status",
            headers={"x-admin-token": settings.admin_token},
        )

    assert response.status_code == 200
    assert set(response.json()) == {"today", "tomorrow", "recent_generation_log"}


@requires_daily_content_status_migration
def test_content_status_reports_safe_revision_logs_and_activity_metadata():
    with _mocked_generation(image_prompt_text="A first hidden package prompt"):
        cg.generate_content_for_date(TEST_DATE)
    cg.publish_content_for_date(TEST_DATE)

    with _mocked_generation(
        image_prompt_text=ORIGINAL_PROMPT,
        revised_prompt_text=REVISED_PROMPT,
        image_bytes=b"replacement-observability-image",
    ):
        replacement = cg.replace_content_for_date(
            TEST_DATE, reason="diagnostics coverage"
        )

    supabase.table("scores").insert({
        "user_id": REAL_TEST_USER_ID,
        "date": TEST_DATE.isoformat(),
        "maths_score": 20,
        "trivia_score": 4,
        "maths_completed": True,
        "trivia_completed": True,
    }).execute()

    response = _admin_get(TEST_DATE.isoformat())
    assert response.status_code == 200
    body = response.json()

    assert body["date"] == TEST_DATE.isoformat()
    content = body["content"]
    assert content["id"]
    assert content["date"] == TEST_DATE.isoformat()
    assert content["status"] == "published"
    assert content["is_fallback"] is False
    assert content["fallback_source_id"] is None
    assert content["content_schema_version"] == cg.CONTENT_SCHEMA_VERSION
    assert content["generator_version"] == cg.GENERATOR_VERSION
    assert content["validated_at"]
    assert content["validation_result"]["valid"] is True
    assert content["package_revision_id"] == replacement["package_revision_id"]
    assert replacement["package_revision_id"] in content["image_storage_key"]
    assert len(content["image_sha256"]) == 64
    assert content["image_model"] == cg.IMAGE_MODEL
    assert content["image_generated_at"]
    assert content["prompt_metadata"] == {
        "original_present": True,
        "effective_present": True,
        "revised_present": True,
    }

    assert len(body["generation_logs"]) == 1
    assert body["generation_logs"][0]["status"] == "success"
    assert len(body["publish_logs"]) == 1
    assert body["publish_logs"][0]["status"] == "success"

    assert len(body["replacement_history"]) == 1
    audit = body["replacement_history"][0]
    assert audit["previous_package_revision_id"] == replacement[
        "previous_package_revision_id"
    ]
    assert audit["replacement_package_revision_id"] == replacement[
        "package_revision_id"
    ]
    assert audit["reason"] == "diagnostics coverage"
    assert audit["forced"] is False

    assert body["activity"] == {
        "score_rows_exist": True,
        "score_row_count": 1,
        "completed_attempts_exist": True,
        "completed_player_count": 1,
        "completed_game_count": 2,
    }

    serialized = json.dumps(body)
    assert ORIGINAL_PROMPT not in serialized
    assert REVISED_PROMPT not in serialized
    assert {
        "image_prompt", "original_image_prompt", "effective_image_prompt"
    }.isdisjoint(set(_all_keys(body)))
