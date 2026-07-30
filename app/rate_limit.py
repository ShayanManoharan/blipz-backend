# rate_limit.py
# Shared slowapi Limiter instance, kept in its own module so both app/main.py (setup)
# and individual routers (the @limiter.limit(...) decorator) can import the same
# object without a circular import.
#
# Uses slowapi's default in-memory storage. This is NOT sufficient once the backend
# runs on more than one process/instance — each instance would track its own
# independent counter, so the effective limit multiplies by instance count. Fine for
# the current single-process local/dev deployment; revisit with a shared store
# (e.g. Redis) before running multiple instances in production.

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
