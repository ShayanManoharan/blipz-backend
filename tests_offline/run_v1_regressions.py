"""Run the unchanged offline-capable V1 tests without live Supabase conftest.

All credentials are synthetic and outbound sockets/DNS are disabled BEFORE app
imports. The migration-gated scoring integration test skips; real integration
coverage remains intentionally unrun. Run this file as a standalone process.
"""
import os
from pathlib import Path
import socket
import sys


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    os.environ.update({
        "SUPABASE_URL": "https://example.invalid",
        "SUPABASE_SECRET_KEY": "offline-synthetic-secret",
        "SUPABASE_ANON_KEY": "offline-synthetic-anon",
        "SUPABASE_JWT_SECRET": "offline-synthetic-jwt-secret",
        "OPENAI_API_KEY": "offline-synthetic-openai-key",
        "ADMIN_TOKEN": "offline-synthetic-admin-token",
        "ENVIRONMENT": "development",
        "CORS_ALLOWED_ORIGINS": "",
        "LOG_LEVEL": "INFO",
    })

    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in offline V1 regression run")

    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden
    socket.getaddrinfo = forbidden

    import pytest

    return pytest.main([
        "--noconftest", "-p", "no:cacheprovider", "-q", "-rs",
        "tests/test_scoring.py",
        "tests/test_content_validation.py",
        "tests/test_content_generator.py",
        "tests/test_guess_scorer.py",
        "tests/test_config.py",
        "tests/test_cron_request.py",
        "tests/test_atomic_completion_contract.py",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
