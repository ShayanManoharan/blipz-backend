# config.py
# Central settings object — loads .env once and validates required vars are present.
# pydantic-settings raises a validation error at import time (i.e. at process startup)
# if any required field is missing — this is what makes "fail clearly if a required
# configuration value is missing" true today for every backend env var, with no extra
# code needed here.

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_secret_key: str
    supabase_anon_key: str
    openai_api_key: str
    admin_token: str
    supabase_jwt_secret: str | None = None

    # "development" | "staging" | "production" — deliberately a free string, not an
    # enum, so an unrecognized value fails loudly via the environment-specific checks
    # in main.py rather than silently coercing to a default.
    environment: str = "development"

    # Comma-separated list of allowed CORS origins. Empty by default — native iOS
    # doesn't send cookies/credentials subject to browser CORS at all, so production
    # needs no origins unless a web admin tool is added later. Only local/admin web
    # tooling in development should ever need this set.
    cors_allowed_origins: str = ""

    log_level: str = "INFO"

    # Set only when running the in-process APScheduler locally for developer
    # convenience (see app/scheduler.py) — staging/production rely on an external
    # cron hitting the protected /admin endpoints instead, so two backend instances
    # never both fire the same scheduled job. Defaults to on for local dev via the
    # environment check in main.py, not this flag directly (see main.py).

    class Config:
        env_file = ".env"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


settings = Settings()
