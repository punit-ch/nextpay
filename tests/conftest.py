"""Test isolation.

* Uses a throw-away SQLite file, never the real ``nextpay.db``.
* Replaces every credential with a dummy value.
* Blocks real network connections, so a test can never reach PayPal or
  OpenRouter even if a mock is forgotten.

The environment is configured BEFORE any ``app`` module is imported.
"""

import os
import socket
import tempfile
from pathlib import Path
from urllib.parse import unquote

_TEST_DIR = Path(tempfile.mkdtemp(prefix="nextpay-tests-"))

os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_DIR / 'test.db').as_posix()}"
os.environ["APP_ENV"] = "test"
os.environ["PAYPAL_CLIENT_ID"] = "test-client-id"
os.environ["PAYPAL_CLIENT_SECRET"] = "test-client-secret"
os.environ["PAYPAL_BASE_URL"] = "https://api-m.sandbox.paypal.com"
os.environ["OPENROUTER_API_KEY"] = "test-openrouter-key"
os.environ["FRONTEND_URL"] = "http://localhost:5173"
os.environ["BACKEND_URL"] = "http://127.0.0.1:8000"
os.environ.pop("PAYPAL_RETURN_URL", None)
os.environ.pop("PAYPAL_CANCEL_URL", None)
os.environ.pop("CORS_ORIGINS", None)

import pytest  # noqa: E402

from app import database  # noqa: E402
from app import models  # noqa: E402,F401  (registers the tables)
from app.rate_limit import limiter  # noqa: E402


_REAL_CONNECT = socket.socket.connect
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """Refuse outbound connections. Loopback stays allowed: asyncio on
    Windows builds its internal socketpair with a loopback connect()."""

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else None
        if host is None or host in _LOCAL_HOSTS:
            return _REAL_CONNECT(self, address)
        raise RuntimeError(f"Real network access is blocked in tests: {host}")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture(autouse=True)
def _fresh_database():
    # Safety net: never wipe anything but the temporary test database.
    # SQLAlchemy percent-encodes Windows drive letters in SQLite URLs, so
    # we decode before comparing paths.
    normalized_url = unquote(str(database.engine.url)).lower()
    assert str(_TEST_DIR.as_posix()).lower() in normalized_url, (
        "Refusing to reset a database outside the test directory."
    )
    database.Base.metadata.drop_all(bind=database.engine)
    database.Base.metadata.create_all(bind=database.engine)
    limiter.reset()
    yield
