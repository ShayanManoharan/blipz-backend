# time_utils.py
# Canonical "what day is it" for the whole app. Fixes PRODUCTION_AUDIT.md's B22: the
# daily reset boundary used to be implicit server-local time (bare `date.today()`
# scattered across games.py/content_generator.py) with no explicit decision recorded.
# UTC is now that explicit decision — every "today"/"tomorrow" the app computes for
# game state or daily content uses these helpers, never a bare date.today()/datetime.now().

from datetime import date, datetime, timedelta, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_today() -> date:
    return utc_now().date()


def utc_tomorrow() -> date:
    return utc_today() + timedelta(days=1)
