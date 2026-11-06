"""Central, environment-driven configuration.

Every value is read at call time (not import time) so that tests and
deployments can change the environment without re-importing modules.
Secrets (PayPal and OpenRouter keys) are read where they are used and are
never logged or returned from here.
"""

import os
from urllib.parse import urlsplit

from dotenv import load_dotenv

# Does not override variables that are already set in the real environment.
load_dotenv()

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _http_url(name: str, default: str) -> str:
    value = _env(name, default).rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"{name} must be an absolute http(s) URL.")
    return value


def app_env() -> str:
    return _env("APP_ENV", "development").lower()


def is_production() -> bool:
    return app_env() == "production"


def database_url() -> str:
    return _env("DATABASE_URL", "sqlite:///./nextpay.db")


def backend_url() -> str:
    return _http_url("BACKEND_URL", "http://127.0.0.1:8000")


def frontend_url() -> str:
    return _http_url("FRONTEND_URL", "http://localhost:5173")


def paypal_return_url() -> str:
    return _http_url("PAYPAL_RETURN_URL", f"{backend_url()}/api/paypal/return")


def paypal_cancel_url() -> str:
    return _http_url("PAYPAL_CANCEL_URL", f"{backend_url()}/api/paypal/cancel")


def cors_origins() -> list[str]:
    """Allowed browser origins: CORS_ORIGINS (comma separated) or the frontend."""
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if raw:
        origins = [item.strip().rstrip("/") for item in raw.split(",")]
    else:
        origins = [frontend_url()]
        if not is_production():
            origins += ["http://localhost:5173", "http://127.0.0.1:5173"]

    unique: list[str] = []
    for origin in origins:
        if origin and origin != "*" and origin not in unique:
            unique.append(origin)
    return unique


def capture_lease_seconds() -> int:
    """How long a `capturing` claim is treated as 'another worker is on it'."""
    try:
        return max(5, int(_env("CAPTURE_LEASE_SECONDS", "60")))
    except ValueError:
        return 60
