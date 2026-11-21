"""Per-identity rate limiting for OpsPilot's sensitive endpoints.

The limiter is deliberately narrow: it protects the expensive investigation
start and the human approval decision, not the whole API. Other read endpoints
keep their existing behaviour so ordinary dashboard and test traffic is
unaffected.

State is process-local because OpsPilot runs exactly one Uvicorn worker against
single-writer SQLite storage. A shared store is only needed if the deployment
model changes, which is documented as a residual risk.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from backend.app.auth.dependencies import get_current_principal
from backend.app.auth.models import Principal

DEFAULT_RATE_LIMIT_REQUESTS = 60
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60.0

MAX_TRACKED_KEYS = 10_000


def env_rate_limit_requests() -> int:
    raw = os.getenv("OPSPILOT_RATE_LIMIT_REQUESTS", "").strip()
    if not raw:
        return DEFAULT_RATE_LIMIT_REQUESTS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_RATE_LIMIT_REQUESTS


def env_rate_limit_window_seconds() -> float:
    raw = os.getenv("OPSPILOT_RATE_LIMIT_WINDOW_SECONDS", "").strip()
    if not raw:
        return DEFAULT_RATE_LIMIT_WINDOW_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_RATE_LIMIT_WINDOW_SECONDS
    return value if value > 0 else DEFAULT_RATE_LIMIT_WINDOW_SECONDS


@dataclass
class _Counter:
    count: int = 0
    reset_at: float = 0.0


class FixedWindowLimiter:
    """Small, thread-safe fixed-window counter keyed by an opaque string."""

    def __init__(self, *, limit: int, window_seconds: float) -> None:
        self.limit = max(0, int(limit))
        self.window_seconds = float(window_seconds)
        self._counters: dict[str, _Counter] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> float | None:
        """Consume one slot for ``key``.

        Returns ``None`` when the request is allowed, or the number of seconds
        the caller should wait when the window is exhausted.
        """
        if self.limit <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            if len(self._counters) > MAX_TRACKED_KEYS:
                self._prune(now)
            counter = self._counters.get(key)
            if counter is None or counter.reset_at <= now:
                self._counters[key] = _Counter(count=1, reset_at=now + self.window_seconds)
                return None
            if counter.count >= self.limit:
                return max(0.0, counter.reset_at - now)
            counter.count += 1
            return None

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()

    def _prune(self, now: float) -> None:
        expired = [key for key, counter in self._counters.items() if counter.reset_at <= now]
        for key in expired:
            del self._counters[key]


def _limiter_for(app: Any, settings: dict[str, Any]) -> FixedWindowLimiter:
    limiters = getattr(app.state, "rate_limiters", None)
    if limiters is None:
        limiters = {}
        app.state.rate_limiters = limiters
    key = (settings["limit"], settings["window_seconds"])
    limiter = limiters.get(key)
    if limiter is None:
        limiter = FixedWindowLimiter(
            limit=settings["limit"], window_seconds=settings["window_seconds"]
        )
        limiters[key] = limiter
    return limiter


def rate_limit(bucket: str):
    """Build a dependency that limits one sensitive operation per principal.

    The chief identity is the authenticated subject. Client address is only a
    fallback should a deployment inject a principal without one.
    """

    def dependency(
        request: Request,
        principal: Principal = Depends(get_current_principal),
    ) -> None:
        settings = getattr(request.app.state, "rate_limit_settings", None)
        if not settings or settings.get("limit", 0) <= 0:
            return
        limiter = _limiter_for(request.app, settings)
        identity = principal.subject or (
            request.client.host if request.client else "unknown"
        )
        retry_after = limiter.retry_after(f"{bucket}:{identity}")
        if retry_after is None:
            return
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            headers={"Retry-After": str(max(1, int(retry_after + 0.999)))},
        )

    return dependency
