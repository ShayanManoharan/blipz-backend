"""Small HTTP client used by hosted cron containers.

The cron services use the same Docker image as the API, but only receive the API
base URL and admin token. Keeping this module independent from ``app.config`` lets
those jobs call the protected endpoints without requiring the web service's other
secrets in their own environments.
"""

from __future__ import annotations

import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


ALLOWED_ENDPOINTS = frozenset({
    "/admin/generate-content",
    "/admin/publish-content",
})
REQUEST_TIMEOUT_SECONDS = 600


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _request_url(base_url: str, endpoint: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("BLIPZ_API_BASE_URL must be a valid HTTP(S) base URL")
    if endpoint not in ALLOWED_ENDPOINTS:
        raise ValueError(f"unsupported cron endpoint: {endpoint}")
    return f"{base_url.rstrip('/')}{endpoint}"


def run(endpoint: str) -> int:
    try:
        base_url = _required_environment("BLIPZ_API_BASE_URL")
        admin_token = _required_environment("ADMIN_TOKEN")
        request_url = _request_url(base_url, endpoint)
    except ValueError as exc:
        print(f"cron request configuration error: {exc}", file=sys.stderr)
        return 2

    request = Request(
        request_url,
        method="POST",
        headers={"x-admin-token": admin_token},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = response.status
    except HTTPError as exc:
        print(f"cron request failed: HTTP {exc.code}", file=sys.stderr)
        return 1
    except (URLError, TimeoutError) as exc:
        print(f"cron request failed: {exc}", file=sys.stderr)
        return 1

    if not 200 <= status < 300:
        print(f"cron request failed: HTTP {status}", file=sys.stderr)
        return 1

    print(f"cron request succeeded: HTTP {status}")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: python -m app.cron_request <endpoint>", file=sys.stderr)
        return 2
    return run(arguments[0])


if __name__ == "__main__":
    raise SystemExit(main())
