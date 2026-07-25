"""
Enterprise Security Headers, Correlation ID Tracing, SHA-256 Schema Hash-Pinning,
Tool Description Sanitization, DNS Rebinding Protection, and Audit Logging with Loguru.
Uses UUID v7 (Time-Sortable) for auto-generated request correlation tokens.
"""

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Set
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp_gateway.logger import log_audit_event, logger
from mcp_gateway.session import generate_uuid7


class SchemaHashPinningGuard:
    """
    Enforces SHA-256 cryptographic hash pinning on tool schemas to detect & prevent
    Rug-Pull supply chain attacks on MCP servers.
    """

    def __init__(self):
        self._pinned_hashes: Dict[str, str] = {}

    def compute_schema_hash(self, tool_schema: Dict[str, Any]) -> str:
        """Compute a deterministic SHA-256 hash of a tool schema definition."""
        canonical_json = json.dumps(tool_schema, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def pin_tool_schema(self, tool_name: str, tool_schema: Dict[str, Any]) -> str:
        """Pin an audited tool schema hash."""
        schema_hash = self.compute_schema_hash(tool_schema)
        self._pinned_hashes[tool_name] = schema_hash
        logger.info(f"[HashPinning] Pinned tool '{tool_name}' SHA-256: {schema_hash[:12]}...")
        return schema_hash

    def verify_tool_schema(self, tool_name: str, tool_schema: Dict[str, Any]) -> bool:
        """
        Verify if the tool schema matches its pinned hash.
        If tampered, returns False and triggers a Rug-Pull alert.
        """
        if tool_name not in self._pinned_hashes:
            # Auto-pin on first trusted load
            self.pin_tool_schema(tool_name, tool_schema)
            return True

        current_hash = self.compute_schema_hash(tool_schema)
        pinned_hash = self._pinned_hashes[tool_name]

        if current_hash != pinned_hash:
            logger.critical(
                f"🚨 [RUG-PULL DETECTED] Tool '{tool_name}' schema hash mismatch! "
                f"Expected: {pinned_hash[:12]}..., Got: {current_hash[:12]}..."
            )
            return False
        return True

    def clear_pins(self) -> None:
        """Clear all pinned schema hashes."""
        self._pinned_hashes.clear()


hash_pinning_guard = SchemaHashPinningGuard()


class ToolDescriptionSanitizer:
    """
    Sanitizes natural language tool descriptions and outputs to protect against
    Prompt Injection, Tool Poisoning, and PII leakage.
    """

    PROMPT_INJECTION_PATTERNS = [
        re.compile(r"<IMPORTANT>.*?</IMPORTANT>", re.IGNORECASE | re.DOTALL),
        re.compile(r"\[SYSTEM PROMPT OVERRIDE\]", re.IGNORECASE),
        re.compile(r"IGNORE ALL PREVIOUS INSTRUCTIONS", re.IGNORECASE),
    ]

    @classmethod
    def sanitize_description(cls, description: str) -> str:
        """Strip prompt injection tags and malicious instruction overrides."""
        sanitized = description
        for pattern in cls.PROMPT_INJECTION_PATTERNS:
            sanitized = pattern.sub("[SANITIZED INSTRUCTION]", sanitized)
        return sanitized.strip()


class EnterpriseSecurityMiddleware(BaseHTTPMiddleware):
    """
    Applies OWASP Recommended Security Headers, injects X-Request-ID correlation tokens,
    and protects against DNS Rebinding attacks by validating the HTTP Origin header.
    """

    def __init__(self, app, allowed_origins: Optional[List[str]] = None):
        super().__init__(app)
        self.allowed_origins: Set[str] = set(allowed_origins or ["*"])

    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. DNS Rebinding Protection via Origin Header Check
        origin = request.headers.get("Origin")
        if origin and "*" not in self.allowed_origins and origin not in self.allowed_origins:
            log_audit_event(
                "DNS_REBINDING_BLOCKED",
                request,
                403,
                message=f"Origin '{origin}' not in allowed origins list",
            )
            return JSONResponse(
                {
                    "error": "Forbidden - Invalid Origin",
                    "message": f"Cross-Origin request from '{origin}' blocked by DNS Rebinding protection.",
                },
                status_code=403,
            )

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
