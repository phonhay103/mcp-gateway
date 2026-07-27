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


def extract_forwardable_headers(request: Request) -> Dict[str, str]:
    """
    Extract all client request headers from Starlette request.
    Preserves and respects 100% of user-provided HTTP headers end-to-end,
    EXCEPT for 'Authorization' headers starting with 'Bearer' or 'bearer' (which are reserved for MCP Gateway auth).
    This guarantees downstream servers cannot view, steal, or interfere with the gateway client's credentials,
    ensuring strict security isolation and zero-trust design.
    """
    forwarded: Dict[str, str] = {}
    for key, value in request.headers.items():
        key_lower = key.lower()
        if key_lower in ("host", "content-length"):
            continue
        # Strip Gateway's client Bearer Token from downstream forwarding for security isolation
        if key_lower == "authorization" and isinstance(value, str) and value.lower().startswith("bearer "):
            continue
        forwarded[key] = value

    # Ensure X-Request-ID correlation token is preserved if present on request state
    request_id = getattr(request.state, "request_id", None)
    if request_id and "x-request-id" not in (k.lower() for k in forwarded):
        forwarded["X-Request-ID"] = request_id

    return forwarded


def filter_forwardable_headers(client_headers: Dict[str, str], allowed_list: Optional[List[str]] = None) -> Dict[str, str]:
    """
    Filters and handles client-supplied headers according to protocol standards and security whitelists.
    
    1. Protocol/Infrastructure Headers:
       - 'X-Request-ID' (Enterprise audit trail / correlation token)
       - 'Traceparent', 'Tracestate' (W3C Distributed Tracing protocol standard)
       These are always kept and preserved if provided by the client.
       
    2. Forbidden/Hop-by-hop Headers:
       - 'host', 'connection', 'content-length', 'content-type', 'keep-alive',
         'proxy-authenticate', 'proxy-authorization', 'te', 'trailer',
         'transfer-encoding', 'upgrade'
       These are always stripped as they are handled dynamically by HTTP transport layer.
       
    3. Gateway Auth Token:
       - 'Authorization: Bearer <token>' is stripped to isolate Gateway and Downstream credentials.
       
    4. Custom Client Headers:
       - Whitelisted using the case-insensitive `allowed_list`. If a client header is in the whitelist,
         it is forwarded. Otherwise, it is stripped to prevent arbitrary header pollution on the backend.
    """
    filtered: Dict[str, str] = {}
    
    # Normalize whitelisted allowed list to lowercase for robust case-insensitive matching
    whitelist = {x.lower() for x in (allowed_list or [])}
    
    # Hop-by-hop HTTP/1.1 transport headers that must not be forwarded
    hop_by_hop = {
        "host", "connection", "content-length", "content-type", "keep-alive",
        "proxy-authenticate", "proxy-authorization", "te", "trailer",
        "transfer-encoding", "upgrade"
    }
    
    # Protocol standard headers that should always pass through if present
    protocol_standards = {"x-request-id", "traceparent", "tracestate"}
    
    for key, value in client_headers.items():
        key_lower = key.lower()
        
        # A. Exclude transport level hop-by-hop headers
        if key_lower in hop_by_hop:
            continue
            
        # B. Strip client-gateway Bearer tokens (security boundary)
        if key_lower == "authorization" and isinstance(value, str) and value.lower().startswith("bearer "):
            continue
            
        # C. Protocol-specific infrastructure headers always pass through
        if key_lower in protocol_standards:
            filtered[key] = value
            continue
            
        # D. For custom client headers, strictly check the whitelisted allowed_list
        if key_lower in whitelist:
            filtered[key] = value

    return filtered
