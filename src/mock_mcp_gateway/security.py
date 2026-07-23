"""
Enterprise Security Headers, Correlation ID Tracing, and Audit Logging with Loguru.
Uses UUID v7 (Time-Sortable) for auto-generated request correlation tokens.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from mock_mcp_gateway.logger import log_audit_event, logger
from mock_mcp_gateway.session import generate_uuid7


class EnterpriseSecurityMiddleware(BaseHTTPMiddleware):
    """
    Applies OWASP Recommended Security Headers and injects X-Request-ID correlation tokens.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Extract or generate correlation request ID using UUID v7
        request_id = request.headers.get("X-Request-ID") or generate_uuid7()
        request.state.request_id = request_id

        response: Response = await call_next(request)

        # Inject Enterprise Security Headers (OWASP Standards)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return response
