# scheduler.py
# Runs the Daily Content Generator automatically at local midnight instead of
# relying on a manual POST to /games/generate-daily-content

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.agents.content_generator import generate_daily_content

logger = logging.getLogger("blipz.scheduler")

scheduler = AsyncIOScheduler()


async def _run_daily_content_job():
    try:
        result = await generate_daily_content()
        logger.info("Daily content job finished: %s", result.get("message"))
    except Exception:
        logger.exception("Daily content generation job failed")


def start_scheduler():
    scheduler.add_job(
        _run_daily_content_job,
        trigger=CronTrigger(hour=0, minute=0),
        id="generate_daily_content",
        replace_existing=True,
    )
    scheduler.start()


def stop_scheduler():
    scheduler.shutdown(wait=False)
