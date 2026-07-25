"""
Tests for Enterprise Middlewares: Rate Limiting and OWASP Security Headers.
"""

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_gateway.rate_limiter import RateLimitMiddleware
from mcp_gateway.security import EnterpriseSecurityMiddleware


def test_owasp_security_headers_and_correlation_id():
    async def endpoint(request):
        return PlainTextResponse("OK")

    app = Starlette(routes=[Route("/test", endpoint)])
    app.add_middleware(EnterpriseSecurityMiddleware)
    client = TestClient(app)

    res = client.get("/test")
    assert res.status_code == 200
    assert "X-Request-ID" in res.headers
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["X-XSS-Protection"] == "1; mode=block"


def test_rate_limiting():
    async def endpoint(request):
        return PlainTextResponse("OK")

    app = Starlette(routes=[Route("/test", endpoint)])
    # Limit to 3 requests per minute
    app.add_middleware(RateLimitMiddleware, requests_per_minute=3, enabled=True)
    client = TestClient(app)

    # First 3 requests should pass
    for _ in range(3):
        res = client.get("/test")
        assert res.status_code == 200

    # 4th request should be rate limited (429)
    res = client.get("/test")
    assert res.status_code == 429
    assert res.headers.get("Retry-After") is not None
    assert "Too Many Requests" in res.json()["error"]
