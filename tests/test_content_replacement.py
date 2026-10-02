"""Focused lifecycle and atomic-replacement regressions for daily content."""

from copy import deepcopy
from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app.agents import content_generator as cg
from app.auth import get_current_user_id
from app.database import supabase
from app.main import app
from tests.conftest import requires_daily_content_status_migration
from tests.test_content_pipeline import _mocked_generation


TEST_DATE = date(2099, 7, 1)
REAL_TEST_USER_ID = "d366ce2a-6cbc-48b9-881c-a4560c9dadf5"


def _cleanup():
    date_str = TEST_DATE.isoformat()
    # The audit FK deliberately protects its content row, so teardown follows the
    # dependency order. These labels/dates are test-only and never overlap real data.
    supabase.table("daily_content_replacement_log").delete().eq("content_date", date_str).execute()
    supabase.table("daily_content_generation_log").delete().eq("content_date", date_str).execute()
    supabase.table("scores").delete().eq("date", date_str).execute()
    supabase.table("daily_content").delete().eq("date", date_str).execute()


@pytest.fixture(autouse=True)
def _isolated_replacement_date():
    _cleanup()
    yield
    app.dependency_overrides.pop(get_current_user_id, None)
    _cleanup()


def _row() -> dict:
    return supabase.table("daily_content").select("*").eq(
        "date", TEST_DATE.isoformat()
    ).execute().data[0]


def _generate_ready(prompt="A first immutable test image"):
    with _mocked_generation(image_prompt_text=prompt) as mocks:
        cg.generate_content_for_date(TEST_DATE)
    return mocks, _row()


@requires_daily_content_status_migration
def test_generic_generation_cannot_overwrite_ready_content():
    _, before = _generate_ready()

    with _mocked_generation(image_prompt_text="A forbidden ready overwrite") as mocks:
        result = cg.generate_content_for_date(TEST_DATE)

    assert result["status"] == "ready"
    assert mocks.images.generate.call_count == 0
    assert _row() == before


@requires_daily_content_status_migration
def test_generic_generation_cannot_overwrite_published_content():
    _generate_ready()
    cg.publish_content_for_date(TEST_DATE)
    before = _row()

    with _mocked_generation(image_prompt_text="A forbidden published overwrite") as mocks:
        result = cg.generate_content_for_date(TEST_DATE)

    assert result["status"] == "published"
    assert mocks.images.generate.call_count == 0
    assert _row() == before


@requires_daily_content_status_migration
def test_replacement_creates_new_revision_key_and_linked_audit_row():
    original_mocks, before = _generate_ready()

    with _mocked_generation(image_prompt_text="A replacement immutable test image") as replacement_mocks:
        result = cg.replace_content_for_date(TEST_DATE, reason="test replacement")

    after = _row()
    assert result["previous_package_revision_id"] == before["package_revision_id"]
    assert result["package_revision_id"] == after["package_revision_id"]
    assert after["status"] == "ready"
    assert after["package_revision_id"] != before["package_revision_id"]
    assert after["image_storage_key"] != before["image_storage_key"]
    assert after["image_url"] != before["image_url"]
    assert before["package_revision_id"] in before["image_storage_key"]
    assert after["package_revision_id"] in after["image_storage_key"]
    original_mocks.storage_bucket.remove.assert_not_called()
    replacement_mocks.storage_bucket.remove.assert_not_called()

    audit = supabase.table("daily_content_replacement_log").select("*").eq(
        "content_date", TEST_DATE.isoformat()
    ).execute().data
    assert len(audit) == 1
    assert audit[0]["daily_content_id"] == before["id"]
    assert audit[0]["previous_package_revision_id"] == before["package_revision_id"]
    assert audit[0]["replacement_package_revision_id"] == after["package_revision_id"]
    assert audit[0]["previous_image_storage_key"] == before["image_storage_key"]
    assert audit[0]["replacement_image_storage_key"] == after["image_storage_key"]
    assert audit[0]["previous_image_sha256"] == before["image_sha256"]
    assert audit[0]["replacement_image_sha256"] == after["image_sha256"]
    assert audit[0]["reason"] == "test replacement"
    assert audit[0]["actor"] == "admin_api"
    assert audit[0]["forced"] is False
    assert audit[0]["completed_attempt_count"] == 0


@requires_daily_content_status_migration
def test_invalid_replacement_leaves_original_untouched_and_unaudited():
    _, before = _generate_ready()
    invalid = deepcopy(before)
    invalid["package_revision_id"] = str(uuid4())
    invalid["image_storage_key"] = f"daily/{TEST_DATE.isoformat()}/{invalid['package_revision_id']}.png"
    invalid["image_url"] = f"https://example.invalid/{invalid['image_storage_key']}"
    invalid["math_problems"] = invalid["math_problems"][:-1]

    with patch.object(cg, "generate_content_package", return_value=invalid):
        with pytest.raises(cg.ContentGenerationError, match="explicit-replacement"):
            cg.replace_content_for_date(TEST_DATE, reason="must fail validation")

    assert _row() == before
    audits = supabase.table("daily_content_replacement_log").select("id").eq(
        "content_date", TEST_DATE.isoformat()
    ).execute().data
    assert audits == []


@requires_daily_content_status_migration
def test_stale_expected_revision_is_rejected_without_mutation():
    _, before = _generate_ready()
    with _mocked_generation(image_prompt_text="A valid but stale replacement"):
        replacement = cg.generate_content_package(TEST_DATE)

    with pytest.raises(APIError, match="revision changed"):
        supabase.rpc("replace_daily_content_revision", {
            "p_daily_content_id": before["id"],
            "p_expected_revision_id": str(uuid4()),
            "p_replacement": replacement,
            "p_actor": "test",
            "p_reason": "stale compare-and-swap test",
            "p_force": False,
        }).execute()

    assert _row() == before
    audits = supabase.table("daily_content_replacement_log").select("id").eq(
        "content_date", TEST_DATE.isoformat()
    ).execute().data
    assert audits == []


@requires_daily_content_status_migration
def test_published_replacement_with_completed_players_requires_force_before_generation():
    _generate_ready()
    cg.publish_content_for_date(TEST_DATE)
    before = _row()
    supabase.table("scores").insert({
        "user_id": REAL_TEST_USER_ID,
        "date": TEST_DATE.isoformat(),
        "maths_score": 1,
        "maths_completed": True,
    }).execute()

    with _mocked_generation(image_prompt_text="Must not spend generation cost") as mocks:
        with pytest.raises(cg.ContentReplacementConflict, match="force=true"):
            cg.replace_content_for_date(TEST_DATE, reason="guard test")

    assert mocks.images.generate.call_count == 0
    assert _row() == before
    audits = supabase.table("daily_content_replacement_log").select("id").eq(
        "content_date", TEST_DATE.isoformat()
    ).execute().data
    assert audits == []


@requires_daily_content_status_migration
def test_guess_review_and_public_payload_use_active_published_revision():
    _generate_ready("The original prompt")
    cg.publish_content_for_date(TEST_DATE)
    with _mocked_generation(image_prompt_text="The active replacement prompt"):
        cg.replace_content_for_date(TEST_DATE, reason="active revision test")

    active = _row()
    supabase.table("scores").insert({
        "user_id": REAL_TEST_USER_ID,
        "date": TEST_DATE.isoformat(),
        "guess_score": 8.0,
        "guess_completed": True,
        "guess_status": "completed",
        "guess_text": "replacement guess",
    }).execute()
    app.dependency_overrides[get_current_user_id] = lambda: REAL_TEST_USER_ID

    with patch("app.routers.games.utc_today", return_value=TEST_DATE), TestClient(app) as client:
        public_response = client.get("/games/daily-content")
        review_response = client.get("/games/guess-review")

    assert public_response.status_code == 200
    assert set(public_response.json()) == {
        "id", "date", "image_url", "math_problems", "trivia_questions"
    }
    assert "image_prompt" not in public_response.json()
    assert public_response.json()["image_url"] == active["image_url"]

    assert review_response.status_code == 200
    assert review_response.json()["actual_prompt"] == active["image_prompt"]
    assert review_response.json()["guess"] == "replacement guess"


def test_replace_content_endpoint_remains_admin_protected():
    with TestClient(app) as client:
        response = client.post(
            "/admin/replace-content",
            params={"content_date": TEST_DATE.isoformat(), "reason": "unauthorized"},
        )
    assert response.status_code in (401, 422)
