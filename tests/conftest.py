# conftest.py
# Shared migration-gating helpers. The generate/publish pipeline (see
# PRODUCTION_AUDIT.md's deployment plan) added daily_content.status, which
# GET /games/daily-content and every /games/submit-* endpoint now filters on — so
# virtually every test that touches a games endpoint depends on it, across several
# test files. Centralized here instead of duplicating the same check per file.

import pytest

from app.database import supabase


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
