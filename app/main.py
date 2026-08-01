# main.py
# FastAPI() — creates the app
# CORSMiddleware — environment-scoped allowed origins (see app/config.py); native iOS
#   doesn't depend on browser CORS at all, this only matters for local/admin web tooling
# include_router — plugs in our game, leaderboard, and admin routes
# /health — liveness only, no external calls, safe for a host's health-check probe
# /health/ready — readiness, confirms the database is actually reachable

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from app.config import settings
from app.database import supabase
from app.logging_config import configure_logging
from app.rate_limit import limiter
from app.routers import games, leaderboard, friends, users, admin_content
from app.scheduler import start_scheduler, stop_scheduler

configure_logging(settings.log_level)
logger = logging.getLogger("blipz.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Blipz API starting (environment=%s)", settings.environment)
    # The in-process midnight scheduler is a local-dev convenience only — staging and
    # production rely on an external cron hitting the protected /admin endpoints (see
    # app/routers/admin_content.py), so multiple hosted instances never both fire the
    # same scheduled job. See PRODUCTION_AUDIT.md's deployment plan.
    if settings.is_development:
        start_scheduler()
        logger.info("In-process scheduler started (development only)")
    else:
        logger.info("In-process scheduler NOT started — relying on external cron (environment=%s)", settings.environment)
    yield
    if settings.is_development:
        stop_scheduler()
    logger.info("Blipz API shutting down")


app = FastAPI(title="Blipz API", description="API for Blipz game", version="1.0.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# No wildcard origin + credentials combination (that pairing is rejected by browsers
# anyway, and was a real misconfiguration before — see PRODUCTION_AUDIT.md B15).
# allow_credentials is only turned on when origins are explicitly configured, since
# native iOS's Authorization header isn't a CORS-credentialed request to begin with.
_cors_origins = settings.cors_allowed_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=bool(_cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(games.router, prefix="/games", tags=["games"])
app.include_router(leaderboard.router, prefix="/leaderboard", tags=["leaderboard"])
app.include_router(friends.router, prefix="/friends", tags=["friends"])
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(admin_content.router, prefix="/admin", tags=["admin"])


@app.get("/")
def root():
    return {"message": "Blipz API is running!"}


@app.get("/health")
def health():
    # Deliberately no DB/OpenAI calls — this is what a host's health-check probe hits
    # on a tight interval, and it must never flap due to a transient dependency issue.
    return {"status": "ok", "environment": settings.environment}


@app.get("/health/ready")
def health_ready():
    try:
        supabase.table("daily_content").select("id").limit(1).execute()
        return {"status": "ready", "environment": settings.environment}
    except Exception as e:
        logger.warning("Readiness check failed: %s", e)
        return JSONResponse(status_code=503, content={"status": "not_ready", "environment": settings.environment})

