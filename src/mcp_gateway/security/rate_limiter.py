"""
Enterprise Rate Limiter Middleware for Mock MCP Gateway.
Implements a Sliding Window algorithm to protect MCP endpoints from abuse.
"""

from collections import defaultdict
import time
from typing import Dict, Optional, Tuple
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimiter:
    """In-memory sliding window rate limiter per client key or IP."""

    def __init__(self, requests_per_minute: int = 120):
        self.requests_per_minute = requests_per_minute
        self.window_size = 60.0  # 60 seconds
        # Map client_id -> list of request timestamps
        self.history: Dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_id: str) -> Tuple[bool, int]:
        """
        Check if client is allowed to make a request.
        Returns (is_allowed, retry_after_seconds).
        """
        now = time.time()
        cutoff = now - self.window_size

        # Clean timestamps older than window_size
        timestamps = [ts for ts in self.history[client_id] if ts > cutoff]
        self.history[client_id] = timestamps

        if len(timestamps) >= self.requests_per_minute:
            oldest = timestamps[0]
            retry_after = int(oldest + self.window_size - now) + 1
            return False, max(1, retry_after)

        self.history[client_id].append(now)
        return True, 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Starlette Middleware to enforce rate limits per client IP or authenticated client.
    """

    def __init__(self, app, requests_per_minute: int = 120, enabled: bool = True):
        super().__init__(app)
        self.enabled = enabled
        self.limiter = RateLimiter(requests_per_minute=requests_per_minute)

    async def dispatch(self, request: Request, call_next):
        if not self.enabled or request.url.path in ["/", "/health", "/favicon.ico"]:
            return await call_next(request)

        # Identify client by authenticated client_name or IP address
        client_id = getattr(request.state, "client_name", None) or (
            request.client.host if request.client else "unknown"
        )

        allowed, retry_after = self.limiter.is_allowed(client_id)
        if not allowed:
            return JSONResponse(
                {
                    "error": "Too Many Requests",
                    "message": f"Rate limit exceeded ({self.limiter.requests_per_minute} req/min). Please try again in {retry_after} seconds.",
                },
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
