# admin_content.py
# Protected internal endpoints for the hosted daily-content pipeline (see
# PRODUCTION_AUDIT.md's deployment plan). Every route requires the admin/cron secret —
# there is no public unauthenticated generation or publication endpoint. Intended
# caller: an external cron (the host's scheduled-job feature, or any HTTPS-capable
# cron service) hitting these on a schedule, not the iOS app.

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app.agents.content_generator import (
    ContentGenerationError,
    ContentReplacementConflict,
    generate_content_for_date,
    publish_content_for_date,
    replace_content_for_date,
    seed_fallback_content,
)
from app.auth import require_admin_token
from app.database import supabase
from app.time_utils import utc_today, utc_tomorrow

router = APIRouter(dependencies=[Depends(require_admin_token)])
logger = logging.getLogger("blipz.admin_content")


def _parse_content_date(content_date: str | None, default: date) -> date:
    if content_date is None:
        return default
    try:
        return date.fromisoformat(content_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="content_date must be YYYY-MM-DD")


@router.post("/generate-content")
def generate_content(content_date: str | None = None):
    """
    Generates (does not publish) content for content_date — defaults to tomorrow
    (UTC), since the whole point is to prepare content before it's needed. Idempotent:
    calling this again for a date that's already 'ready' or 'published' is a no-op.
    """
    target_date = _parse_content_date(content_date, utc_tomorrow())
    try:
        return generate_content_for_date(target_date)
    except ContentGenerationError as e:
        raise HTTPException(status_code=502, detail=f"Content generation failed: {e}")


@router.post("/publish-content")
def publish_content(content_date: str | None = None):
    """
    Publishes content for content_date — defaults to today (UTC), the daily boundary.
    Idempotent: publishing an already-published date is a no-op success. Activates a
    fallback package automatically if nothing is 'ready' yet.
    """
    target_date = _parse_content_date(content_date, utc_today())
    try:
        return publish_content_for_date(target_date)
    except ContentGenerationError as e:
        raise HTTPException(status_code=502, detail=f"Publication failed: {e}")


@router.post("/replace-content")
def replace_content(content_date: str, reason: str, force: bool = False):
    """
    Explicit, audited replacement for ready/published content. Published dates with
    completed attempts require force=true so fairness impact cannot be accidental.
    The database swaps the validated package and inserts its audit row atomically.
    """
    target_date = _parse_content_date(content_date, utc_today())
    logger.warning(
        "Administrative content replacement requested (content_date=%s, forced=%s)",
        target_date,
        force,
    )
    try:
        return replace_content_for_date(target_date, reason=reason, force=force)
    except ContentReplacementConflict as e:
        raise HTTPException(status_code=409, detail=f"Content replacement blocked: {e}")
    except ContentGenerationError as e:
        raise HTTPException(status_code=502, detail=f"Content replacement failed: {e}")


@router.get("/content-status")
def content_status():
    """
    Observability: today's/tomorrow's readiness plus the most recent generation log
    entries — powers on-call visibility without needing direct DB access.
    """
    today = utc_today().isoformat()
    tomorrow = utc_tomorrow().isoformat()

    today_row = supabase.table("daily_content").select("status, is_fallback, published_at").eq("date", today).execute()
    tomorrow_row = supabase.table("daily_content").select("status, is_fallback, generated_at").eq("date", tomorrow).execute()
    recent_log = (
        supabase.table("daily_content_generation_log")
        .select("*")
        .order("attempted_at", desc=True)
        .limit(10)
        .execute()
    )

    return {
        "today": {"date": today, **(today_row.data[0] if today_row.data else {"status": "missing"})},
        "tomorrow": {"date": tomorrow, **(tomorrow_row.data[0] if tomorrow_row.data else {"status": "missing"})},
        "recent_generation_log": recent_log.data,
    }


@router.post("/seed-fallback-content")
def seed_fallback(placeholder_image_url: str):
    """
    One-time (idempotent) setup of the emergency fallback pool. Does not call
    OpenAI — see seed_fallback_content's docstring.
    """
    return seed_fallback_content(placeholder_image_url)
