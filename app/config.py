# config.py
# Central settings object — loads .env once and validates required vars are present

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_secret_key: str
    supabase_anon_key: str
    openai_api_key: str
    admin_token: str
    supabase_jwt_secret: str | None = None

    class Config:
        env_file = ".env"


settings = Settings()
