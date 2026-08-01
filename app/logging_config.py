# logging_config.py
# One-time structured logging setup for the whole process (call configure_logging()
# once, at startup — see main.py's lifespan).
#
# Deliberate exclusions (never log these, anywhere in the app):
#   - API keys / secrets (OPENAI_API_KEY, ADMIN_TOKEN, Supabase keys)
#   - full auth tokens/JWTs — log user_id (already just a UUID) instead
#   - raw user Guess text — log length/outcome, not the content, outside of narrow
#     debug-only paths
#   - hidden image prompts (the Guess answer) in routine (INFO+) logs
#
# Format is key=value rather than JSON — trivial to grep locally, and every hosting
# option compared in PRODUCTION_AUDIT.md's deployment section (Render/Railway/Fly.io)
# ingests plain stdout lines fine without needing a JSON log shipper configured first.

import logging
import sys

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers = [handler]

    # Quiet noisy third-party loggers down to WARNING regardless of our own level, so
    # e.g. LOG_LEVEL=DEBUG locally doesn't get drowned in httpx/urllib3 request traces.
    for noisy in ("httpx", "httpcore", "urllib3", "apscheduler"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logging.getLogger().level))

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
