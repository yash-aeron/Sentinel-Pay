"""In-process rate limiting and request metrics.

Both are deliberately dependency-free and per-process. A single uvicorn
worker makes them exact; multiple workers would each keep their own buckets,
which loosens the limit but never blocks a legitimate client entirely. The
production answer (shared Redis buckets, Prometheus scrape) is documented in
``docs/design.md``.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# (method, path) -> which settings key sets its per-minute budget.
RATE_LIMITED_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/transactions"): "rate_limit_payments_per_minute",
    ("POST", "/copilot/ask"): "rate_limit_copilot_per_minute",
}

WINDOW_SECONDS = 60.0


class Metrics:
    """Coarse counters for the /metrics endpoint.

    Routes are labelled by template (``/transactions``), never by path
    parameters, so card numbers of labels cannot grow without bound.
    """

    def __init__(self) -> None:
        self._counters: dict[tuple[str, str, int], int] = defaultdict(int)
        self.started_at = time.time()

    def observe(self, method: str, route: str, status_code: int) -> None:
        self._counters[(method, route, status_code)] += 1

    def render(self) -> str:
        lines = [
            "# HELP sentinelpay_requests_total Requests by method, route and status.",
            "# TYPE sentinelpay_requests_total counter",
        ]
        for (method, route, status_code), value in sorted(self._counters.items()):
            lines.append(
                f'sentinelpay_requests_total{{method="{method}",route="{route}",'
                f'code="{status_code}"}} {value}'
            )
        lines.append(
            "# HELP sentinelpay_uptime_seconds Seconds since the process started."
        )
        lines.append("# TYPE sentinelpay_uptime_seconds gauge")
        lines.append(f"sentinelpay_uptime_seconds {time.time() - self.started_at:.0f}")
        return "\n".join(lines) + "\n"


def _client_key(request: Request) -> str:
    # Real auth lands with Phase 5's replacement of the API-key stand-in; the
    # api key header is the finest stable identity available today.
    return request.headers.get("X-API-Key") or "default"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window limiter for the money and model endpoints."""

    def __init__(self, app: Any, limits: dict[tuple[str, str], int]) -> None:
        super().__init__(app)
        self._limits = limits
        self._windows: dict[tuple[str, str, str], deque[float]] = defaultdict(deque)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        route = (request.method, request.url.path)
        limit = self._limits.get(route)
        if limit is None:
            return await call_next(request)

        key = (request.method, request.url.path, _client_key(request))
        now = time.monotonic()
        bucket = self._windows[key]
        while bucket and now - bucket[0] > WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(WINDOW_SECONDS - (now - bucket[0])))
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "too many requests; slow down",
                        "context": {"retry_after_seconds": retry_after},
                    }
                },
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
        return await call_next(request)


class MetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, metrics: Metrics) -> None:
        super().__init__(app)
        self._metrics = metrics

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        # Normalise parameterised paths so label cardinality stays fixed.
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        self._metrics.observe(request.method, path, response.status_code)
        return response


__all__ = [
    "Metrics",
    "MetricsMiddleware",
    "RATE_LIMITED_ROUTES",
    "RateLimitMiddleware",
]
