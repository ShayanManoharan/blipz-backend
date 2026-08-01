# Tests for the hosted-deployment daily-content pipeline (see PRODUCTION_AUDIT.md's
# deployment plan): separating generation from publication, idempotency, validation
# gates, and fallback activation. Uses a far-future content_date so it never collides
# with real daily content, and mocks OpenAI (never spends real API cost) plus the
# Supabase storage calls and the reachability check, while still exercising real reads/
# writes against daily_content / daily_content_generation_log / fallback_daily_content
# — consistent with this project's "real Supabase, no test-DB isolation" convention.

import base64
import json
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.agents import content_generator as cg
from app.auth import get_current_user_id
from app.database import supabase
from app.main import app
from app.time_utils import utc_today, utc_tomorrow
from tests.conftest import requires_daily_content_status_migration

REAL_TEST_USER_ID = "d366ce2a-6cbc-48b9-881c-a4560c9dadf5"

TEST_CONTENT_DATE = date(2099, 6, 15)
TEST_CONTENT_DATE_2 = date(2099, 6, 16)


def _cleanup(content_date: date = TEST_CONTENT_DATE):
    # Best-effort: the tables this touches are only present once the migration below
    # is applied. A handful of tests in this file (health/admin-auth checks) don't
    # need that migration at all, and share this same autouse fixture — so cleanup
    # must no-op gracefully rather than erroring for them.
    try:
        supabase.table("daily_content").delete().eq("date", content_date.isoformat()).execute()
        supabase.table("daily_content_generation_log").delete().eq("content_date", content_date.isoformat()).execute()
    except Exception:
        pass


def _cleanup_fallback_pool():
    try:
        supabase.table("fallback_daily_content").delete().like("label", "test-fallback-%").execute()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clean_before_and_after():
    _cleanup(TEST_CONTENT_DATE)
    _cleanup(TEST_CONTENT_DATE_2)
    _cleanup_fallback_pool()
    yield
    _cleanup(TEST_CONTENT_DATE)
    _cleanup(TEST_CONTENT_DATE_2)
    _cleanup_fallback_pool()


def _valid_trivia_payload():
    return json.dumps([
        {"question": f"Test question {i}?", "category": "General",
         "options": ["Alpha", "Beta", "Gamma", "Delta"], "answer": "A"}
        for i in range(5)
    ])


def _mock_openai_client(trivia_text=None, image_prompt_text="A cat wearing sunglasses on a surfboard"):
    client = MagicMock()
    prompt_response = MagicMock()
    prompt_response.choices = [MagicMock(message=MagicMock(content=image_prompt_text))]
    trivia_response = MagicMock()
    trivia_response.choices = [MagicMock(message=MagicMock(content=trivia_text or _valid_trivia_payload()))]
    # First call = image prompt, subsequent calls = trivia (possibly retried once)
    client.chat.completions.create.side_effect = [prompt_response, trivia_response, trivia_response]

    image_response = MagicMock()
    image_response.data = [MagicMock(b64_json=base64.b64encode(b"fake-png-bytes").decode())]
    client.images.generate.return_value = image_response
    return client


@contextmanager
def _mocked_generation(trivia_text=None, image_prompt_text=None, storage_upload_side_effect=None, head_status=200):
    mock_client = _mock_openai_client(
        trivia_text=trivia_text, image_prompt_text=image_prompt_text or "A cat wearing sunglasses on a surfboard"
    )
    mock_storage_bucket = MagicMock()
    if storage_upload_side_effect:
        mock_storage_bucket.upload.side_effect = storage_upload_side_effect
    mock_storage_bucket.get_public_url.return_value = "https://example.invalid/fake-daily-image.png"

    mock_head_response = MagicMock(status_code=head_status)

    with patch.object(cg, "openai_client", mock_client), \
         patch.object(supabase.storage, "from_", return_value=mock_storage_bucket), \
         patch.object(cg.httpx, "head", return_value=mock_head_response):
        yield mock_client


# --- Health endpoint needs no migration/auth at all --------------------------------


def test_health_endpoint_works_without_auth_or_migration():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# --- Protected admin endpoints reject invalid/missing secrets ----------------------


def test_generate_endpoint_rejects_missing_admin_token():
    with TestClient(app) as client:
        response = client.post("/admin/generate-content")
    assert response.status_code in (401, 422)


def test_generate_endpoint_rejects_invalid_admin_token():
    with TestClient(app) as client:
        response = client.post("/admin/generate-content", headers={"x-admin-token": "definitely-wrong"})
    assert response.status_code == 401


# --- Idempotent generation ----------------------------------------------------------


@requires_daily_content_status_migration
def test_generate_content_for_date_is_idempotent():
    with _mocked_generation() as mock_client:
        first = cg.generate_content_for_date(TEST_CONTENT_DATE)
        assert first["status"] == "ready"
        assert mock_client.images.generate.call_count == 1

        second = cg.generate_content_for_date(TEST_CONTENT_DATE)
        assert second["status"] == "ready"
        # No additional OpenAI calls — the existing 'ready' row short-circuited.
        assert mock_client.images.generate.call_count == 1

    rows = supabase.table("daily_content").select("*").eq("date", TEST_CONTENT_DATE.isoformat()).execute().data
    assert len(rows) == 1  # UNIQUE(date) — never two rows for the same content_date


@requires_daily_content_status_migration
def test_duplicate_invocation_does_not_duplicate_rows():
    # Simulates a cron firing the same generation twice (e.g. a retried job) —
    # equivalent scenario to test_generate_content_for_date_is_idempotent but phrased
    # around the "duplicate scheduler invocation" requirement specifically.
    with _mocked_generation():
        cg.generate_content_for_date(TEST_CONTENT_DATE)
        cg.generate_content_for_date(TEST_CONTENT_DATE)
        cg.generate_content_for_date(TEST_CONTENT_DATE)

    rows = supabase.table("daily_content").select("id").eq("date", TEST_CONTENT_DATE.isoformat()).execute().data
    assert len(rows) == 1


# --- Validation gates: no partial package ever gets stored --------------------------


@requires_daily_content_status_migration
def test_missing_image_prompt_raises_and_stores_nothing():
    with _mocked_generation(image_prompt_text="   "):  # blank after strip()
        with pytest.raises(cg.ContentGenerationError):
            cg.generate_content_for_date(TEST_CONTENT_DATE)

    rows = supabase.table("daily_content").select("id").eq("date", TEST_CONTENT_DATE.isoformat()).execute().data
    assert rows == []

    log = supabase.table("daily_content_generation_log").select("status").eq(
        "content_date", TEST_CONTENT_DATE.isoformat()
    ).execute().data
    assert log and log[0]["status"] == "failed"


@requires_daily_content_status_migration
def test_invalid_trivia_output_raises_and_stores_nothing():
    with _mocked_generation(trivia_text=json.dumps([{"question": "only one question"}])):
        with pytest.raises(cg.ContentGenerationError):
            cg.generate_content_for_date(TEST_CONTENT_DATE)

    rows = supabase.table("daily_content").select("id").eq("date", TEST_CONTENT_DATE.isoformat()).execute().data
    assert rows == []


@requires_daily_content_status_migration
def test_storage_upload_failure_raises_and_stores_nothing():
    with _mocked_generation(storage_upload_side_effect=RuntimeError("simulated storage outage")):
        with pytest.raises(RuntimeError):
            cg.generate_content_for_date(TEST_CONTENT_DATE)

    rows = supabase.table("daily_content").select("id").eq("date", TEST_CONTENT_DATE.isoformat()).execute().data
    assert rows == []


@requires_daily_content_status_migration
def test_unreachable_uploaded_image_raises_and_stores_nothing():
    # Upload call itself succeeds, but the resulting public URL isn't fetchable —
    # must be caught before content is considered ready, not discovered later by a
    # player's broken image load.
    with _mocked_generation(head_status=404):
        with pytest.raises(cg.ContentGenerationError):
            cg.generate_content_for_date(TEST_CONTENT_DATE)

    rows = supabase.table("daily_content").select("id").eq("date", TEST_CONTENT_DATE.isoformat()).execute().data
    assert rows == []


# --- Publication: with and without prepared content ---------------------------------


@requires_daily_content_status_migration
def test_publish_with_ready_content_succeeds():
    with _mocked_generation():
        cg.generate_content_for_date(TEST_CONTENT_DATE)

    result = cg.publish_content_for_date(TEST_CONTENT_DATE)
    assert result["status"] == "published"
    assert result["used_fallback"] is False

    row = supabase.table("daily_content").select("status, published_at").eq(
        "date", TEST_CONTENT_DATE.isoformat()
    ).execute().data[0]
    assert row["status"] == "published"
    assert row["published_at"] is not None


@requires_daily_content_status_migration
def test_publish_is_idempotent():
    with _mocked_generation():
        cg.generate_content_for_date(TEST_CONTENT_DATE)

    first = cg.publish_content_for_date(TEST_CONTENT_DATE)
    second = cg.publish_content_for_date(TEST_CONTENT_DATE)
    assert first["status"] == second["status"] == "published"

    rows = supabase.table("daily_content").select("id").eq("date", TEST_CONTENT_DATE.isoformat()).execute().data
    assert len(rows) == 1


@requires_daily_content_status_migration
def test_publish_without_ready_content_activates_fallback():
    supabase.table("fallback_daily_content").insert({
        "label": "test-fallback-only",
        "image_url": "https://example.invalid/fallback.png",
        "image_prompt": "A test fallback image prompt",
        "trivia_questions": cg.normalize_trivia_questions(json.loads(_valid_trivia_payload())),
        "math_problems": cg.generate_math_problems(20),
    }).execute()

    result = cg.publish_content_for_date(TEST_CONTENT_DATE)
    assert result["status"] == "published"
    assert result["used_fallback"] is True

    row = supabase.table("daily_content").select("is_fallback, status").eq(
        "date", TEST_CONTENT_DATE.isoformat()
    ).execute().data[0]
    assert row["is_fallback"] is True
    assert row["status"] == "published"


@requires_daily_content_status_migration
def test_publish_without_ready_content_and_without_fallback_raises():
    # No fallback rows exist (cleaned by the autouse fixture) — must fail clearly
    # rather than silently publishing nothing.
    with pytest.raises(cg.ContentGenerationError):
        cg.publish_content_for_date(TEST_CONTENT_DATE)

    rows = supabase.table("daily_content").select("id").eq("date", TEST_CONTENT_DATE.isoformat()).execute().data
    assert rows == []


@requires_daily_content_status_migration
def test_fallback_does_not_repeat_previous_day_when_alternative_exists():
    fb_a = supabase.table("fallback_daily_content").insert({
        "label": "test-fallback-a",
        "image_url": "https://example.invalid/a.png",
        "image_prompt": "Fallback A prompt",
        "trivia_questions": cg.normalize_trivia_questions(json.loads(_valid_trivia_payload())),
        "math_problems": cg.generate_math_problems(20),
    }).execute().data[0]
    fb_b = supabase.table("fallback_daily_content").insert({
        "label": "test-fallback-b",
        "image_url": "https://example.invalid/b.png",
        "image_prompt": "Fallback B prompt",
        "trivia_questions": cg.normalize_trivia_questions(json.loads(_valid_trivia_payload())),
        "math_problems": cg.generate_math_problems(20),
    }).execute().data[0]

    first = cg.activate_fallback_for_date(TEST_CONTENT_DATE)
    second = cg.activate_fallback_for_date(TEST_CONTENT_DATE_2)  # "the next day"

    first_row = supabase.table("daily_content").select("fallback_source_id").eq(
        "date", TEST_CONTENT_DATE.isoformat()
    ).execute().data[0]
    second_row = supabase.table("daily_content").select("fallback_source_id").eq(
        "date", TEST_CONTENT_DATE_2.isoformat()
    ).execute().data[0]

    assert first_row["fallback_source_id"] != second_row["fallback_source_id"]
    assert {first_row["fallback_source_id"], second_row["fallback_source_id"]} == {fb_a["id"], fb_b["id"]}


# --- UTC day-boundary behavior -------------------------------------------------------


def test_utc_today_and_tomorrow_are_consistent():
    assert utc_tomorrow() == utc_today() + timedelta(days=1)


# --- status defaults to 'draft', and only 'published' is player-visible -------------
# See PRODUCTION_AUDIT.md's deployment plan: 'draft' is the fail-safe default so an
# insert that forgets to specify status can never accidentally become publicly
# servable — only an explicit transition to 'published' (via publish_content_for_date
# or activate_fallback_for_date) makes a row visible to GET /games/daily-content.

HISTORICAL_PUBLISHED_DATES = ["2026-05-31", "2026-06-01", "2026-07-29", "2026-07-30", "2026-07-31"]


@contextmanager
def _with_todays_status(new_status: str):
    """
    Temporarily forces today's (UTC) daily_content row to `new_status`, restoring the
    original row/status afterward — or, if no row exists for today yet, inserts a
    throwaway one and removes it afterward. Mirrors the same swap-and-restore pattern
    tests/test_trivia_grading.py already uses for today's real content row.
    """
    today_str = utc_today().isoformat()
    existing = supabase.table("daily_content").select("*").eq("date", today_str).execute().data

    if existing:
        original_status = existing[0]["status"]
        supabase.table("daily_content").update({"status": new_status}).eq("date", today_str).execute()
        try:
            yield
        finally:
            supabase.table("daily_content").update({"status": original_status}).eq("date", today_str).execute()
    else:
        supabase.table("daily_content").insert({
            "date": today_str,
            "image_url": "https://example.invalid/test.png",
            "image_prompt": "test prompt",
            "trivia_questions": cg.normalize_trivia_questions(json.loads(_valid_trivia_payload())),
            "math_problems": cg.generate_math_problems(20),
            "status": new_status,
        }).execute()
        try:
            yield
        finally:
            supabase.table("daily_content").delete().eq("date", today_str).execute()


def _auth():
    app.dependency_overrides[get_current_user_id] = lambda: REAL_TEST_USER_ID


def _clear_auth():
    app.dependency_overrides.pop(get_current_user_id, None)


@requires_daily_content_status_migration
def test_insert_omitting_status_becomes_draft():
    date_str = TEST_CONTENT_DATE.isoformat()
    supabase.table("daily_content").insert({
        "date": date_str,
        "image_url": "https://example.invalid/test.png",
        "image_prompt": "test prompt",
        "trivia_questions": cg.normalize_trivia_questions(json.loads(_valid_trivia_payload())),
        "math_problems": cg.generate_math_problems(20),
        # status deliberately omitted — must fall back to the column DEFAULT, not
        # whatever the application layer would have chosen.
    }).execute()

    row = supabase.table("daily_content").select("status").eq("date", date_str).execute().data[0]
    assert row["status"] == "draft"


@requires_daily_content_status_migration
def test_draft_content_is_not_player_visible():
    with _with_todays_status("draft"):
        _auth()
        try:
            with TestClient(app) as client:
                response = client.get("/games/daily-content")
        finally:
            _clear_auth()
    assert response.status_code == 404


@requires_daily_content_status_migration
def test_ready_content_is_not_player_visible():
    with _with_todays_status("ready"):
        _auth()
        try:
            with TestClient(app) as client:
                response = client.get("/games/daily-content")
        finally:
            _clear_auth()
    assert response.status_code == 404


@requires_daily_content_status_migration
def test_published_content_is_player_visible():
    with _with_todays_status("published"):
        _auth()
        try:
            with TestClient(app) as client:
                response = client.get("/games/daily-content")
        finally:
            _clear_auth()
    assert response.status_code == 200


@requires_daily_content_status_migration
def test_historical_rows_are_published_after_backfill():
    rows = supabase.table("daily_content").select("date, status").in_(
        "date", HISTORICAL_PUBLISHED_DATES
    ).execute().data
    assert len(rows) == len(HISTORICAL_PUBLISHED_DATES)
    for row in rows:
        assert row["status"] == "published", f"{row['date']} should be 'published' after backfill, got {row['status']!r}"
