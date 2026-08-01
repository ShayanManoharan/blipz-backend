# Unit tests for the environment/CORS settings added for hosted deployment (see
# PRODUCTION_AUDIT.md's deployment plan / docs/DEPLOYMENT.md). Pure logic, no network,
# no migration dependency — constructs Settings directly rather than touching the
# process-wide `app.config.settings` singleton.

from app.config import Settings


def _settings(**overrides):
    base = {
        "supabase_url": "https://example.invalid",
        "supabase_secret_key": "secret",
        "supabase_anon_key": "anon",
        "openai_api_key": "sk-test",
        "admin_token": "admin-test",
    }
    base.update(overrides)
    return Settings(**base)


def test_is_development_true_by_default():
    assert _settings().is_development is True


def test_is_development_false_for_staging_and_production():
    assert _settings(environment="staging").is_development is False
    assert _settings(environment="production").is_development is False


def test_cors_allowed_origins_list_empty_by_default():
    assert _settings().cors_allowed_origins_list == []


def test_cors_allowed_origins_list_parses_comma_separated_and_trims_whitespace():
    settings = _settings(cors_allowed_origins="https://a.example.com, https://b.example.com ,")
    assert settings.cors_allowed_origins_list == ["https://a.example.com", "https://b.example.com"]
