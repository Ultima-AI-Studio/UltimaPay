"""Environment-backed service settings."""

from __future__ import annotations

import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def host() -> str:
    return os.getenv("ULTIMAPAY_HOST", "127.0.0.1")


def port() -> int:
    return int(os.getenv("ULTIMAPAY_PORT", "8061"))


def database_url() -> str:
    return os.getenv(
        "ULTIMAPAY_DATABASE_URL",
        f"sqlite:///{(BACKEND_ROOT / 'ultimapay.db').as_posix()}",
    )


def user_auth_secret() -> str:
    return os.getenv("ULTIMAPAY_USER_AUTH_SECRET", "")


def admin_token() -> str:
    return os.getenv("ULTIMAPAY_ADMIN_TOKEN", "")

