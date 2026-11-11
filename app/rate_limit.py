"""Small process-local fixed-window rate limiter for public expensive endpoints.

For multi-worker or multi-instance production deployments, replace this store
with a shared Redis-backed limiter. Never trust arbitrary X-Forwarded-For
headers here; use the ASGI client's address after trusted-proxy configuration.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from time import monotonic

from fastapi import Request
from fastapi.responses import JSONResponse


class FixedWindowRateLimiter:
    def __init__(self) -> None:
        self._lock = Lock()
        self._buckets: dict[tuple[str, str], tuple[float, int]] = {}

    def reset(self) -> None:
        """Clear counters; exposed for isolated automated tests."""
        with self._lock:
            self._buckets.clear()

    def check(
        self,
        *,
        key: str,
        scope: str,
        limit: int,
        window_seconds: int = 60,
    ) -> int | None:
        """Return retry-after seconds when over limit, otherwise None."""
        now = monotonic()
        bucket_key = (scope, key)

        with self._lock:
            # Opportunistically discard expired buckets to bound memory use.
            expired = [
                item
                for item, (started, _count) in self._buckets.items()
                if now - started >= window_seconds
            ]
            for item in expired:
                self._buckets.pop(item, None)

            started, count = self._buckets.get(bucket_key, (now, 0))
            if now - started >= window_seconds:
                started, count = now, 0

            if count >= limit:
                return max(1, int(window_seconds - (now - started)))

            self._buckets[bucket_key] = (started, count + 1)
            return None


limiter = FixedWindowRateLimiter()

# Limits are per client IP and per endpoint, using fixed 60-second windows.
RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/api/agent/recommend": (10, 60),
    "/api/plans": (20, 60),
}


async def rate_limit_middleware(request: Request, call_next: Callable):
    rule = RATE_LIMITS.get(request.url.path)
    if rule and request.method == "POST":
        limit, window = rule
        client_ip = request.client.host if request.client else "unknown"
        retry_after = limiter.check(
            key=client_ip,
            scope=request.url.path,
            limit=limit,
            window_seconds=window,
        )
        if retry_after is not None:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please try again shortly."
                },
                headers={"Retry-After": str(retry_after)},
            )

    return await call_next(request)
