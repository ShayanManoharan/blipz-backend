from urllib.error import HTTPError, URLError

import pytest

from app import cron_request


class SuccessfulResponse:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


@pytest.fixture
def cron_environment(monkeypatch):
    monkeypatch.setenv("BLIPZ_API_BASE_URL", "https://staging.example.test/")
    monkeypatch.setenv("ADMIN_TOKEN", "local-test-admin-token")


@pytest.mark.parametrize("endpoint", sorted(cron_request.ALLOWED_ENDPOINTS))
def test_cron_request_posts_with_admin_token(monkeypatch, cron_environment, endpoint):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return SuccessfulResponse()

    monkeypatch.setattr(cron_request, "urlopen", fake_urlopen)

    assert cron_request.main([endpoint]) == 0
    assert captured["request"].full_url == f"https://staging.example.test{endpoint}"
    assert captured["request"].method == "POST"
    assert captured["request"].get_header("X-admin-token") == "local-test-admin-token"
    assert captured["timeout"] == cron_request.REQUEST_TIMEOUT_SECONDS


@pytest.mark.parametrize("missing_name", ["BLIPZ_API_BASE_URL", "ADMIN_TOKEN"])
def test_cron_request_rejects_missing_environment(monkeypatch, cron_environment, missing_name):
    monkeypatch.delenv(missing_name)

    assert cron_request.main(["/admin/generate-content"]) == 2


@pytest.mark.parametrize(
    "base_url",
    ["not-a-url", "ftp://example.test", "https://example.test?unsafe=true"],
)
def test_cron_request_rejects_invalid_base_url(monkeypatch, cron_environment, base_url):
    monkeypatch.setenv("BLIPZ_API_BASE_URL", base_url)

    assert cron_request.main(["/admin/generate-content"]) == 2


def test_cron_request_rejects_unapproved_endpoint(cron_environment):
    assert cron_request.main(["/admin/replace-content"]) == 2


def test_cron_request_returns_nonzero_for_http_failure(monkeypatch, cron_environment):
    def fail_with_unauthorized(request, timeout):
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(cron_request, "urlopen", fail_with_unauthorized)

    assert cron_request.main(["/admin/publish-content"]) == 1


def test_cron_request_returns_nonzero_for_network_failure(monkeypatch, cron_environment):
    def fail_to_connect(request, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(cron_request, "urlopen", fail_to_connect)

    assert cron_request.main(["/admin/publish-content"]) == 1
