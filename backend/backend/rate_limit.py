"""In-memory sliding-window rate limiter.

Used for auth and command endpoints. Limits are read from the config so they
can be tuned at runtime; the limiter keeps per-client counters for a window.
"""

import threading
import time

from fastapi import Request

from .config import get_config

_lock = threading.Lock()
_hits: dict = {}  # (client, window_start) -> count


def _client(request):
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(request, limit=None):
    """Return True if allowed, False if rate-limited."""
    limit = limit or get_config().get("security.rate_limit_per_minute", 120)
    key = _client(request)
    now = time.time()
    window = int(now // 60)
    with _lock:
        counts = _hits.setdefault(key, {})
        if window not in counts:
            counts.clear()
            counts[window] = 0
        counts[window] += 1
        # periodically trim stale entries
        if len(_hits) > 10_000:
            for k in list(_hits):
                if window - max(_hits[k]) > 2:
                    del _hits[k]
        return counts[window] <= limit


def apply_rate_limit(request: Request):
    from fastapi import HTTPException

    if not check_rate_limit(request):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
