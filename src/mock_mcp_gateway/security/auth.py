"""
Common Enterprise Authentication Layer for Mock MCP Gateway.
Supports API Key (Header only), JWT Validation, Timing Attack Protection,
Granular Per-Call Tool Execution Authorization, and Loguru Audit Logging.
Enforces removal of weak protocols (e.g. Query Parameter authentication).
Pydantic v2 Models used for configuration and client credential management.
"""

import hmac
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import jwt
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from mock_mcp_gateway.logger import log_audit_event, logger


class APIKeyInfo(BaseModel):
    """Represents a client credentials record parsed via Pydantic v2."""

    name: str = Field(default="client", description="Name of the client application")
    key: str = Field(..., description="API Secret key")
    allowed_servers: Set[str] = Field(
        default_factory=lambda: {"*"},
        description="Set of allowed server names or {'*'} for all",
    )
    allowed_tools: Set[str] = Field(
        default_factory=lambda: {"*"},
        description="Set of allowed tool names/patterns or {'*'} for all",
    )

    @field_validator("allowed_servers", mode="before")
    @classmethod
    def validate_allowed_servers(cls, v) -> Set[str]:
        if isinstance(v, list):
            return set(v)
        if isinstance(v, str):
            return {v}
        if isinstance(v, set):
            return v
        return {"*"}

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def validate_allowed_tools(cls, v) -> Set[str]:
        if isinstance(v, list):
            return set(v)
        if isinstance(v, str):
            return {v}
        if isinstance(v, set):
            return v
        return {"*"}

    def can_access(self, server_name: str) -> bool:
        return "*" in self.allowed_servers or server_name in self.allowed_servers

    def can_execute_tool(self, tool_name: str) -> bool:
        """Check if client credentials permit executing a specific tool."""
        if "*" in self.allowed_tools or "all" in self.allowed_tools:
            return True
        if tool_name in self.allowed_tools:
            return True
        for pattern in self.allowed_tools:
            if pattern.endswith("*") and tool_name.startswith(pattern[:-1]):
                return True
        return False


class PerCallAuthorizer:
    """
    Granular Per-Call Authorization Engine evaluating claims for JSON-RPC tool executions.
    """

    @staticmethod
    def authorize_tool_call(
        key_info: APIKeyInfo,
        tool_name: str,
        tool_args: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """
        Evaluates whether the authenticated client is permitted to execute a specific tool call.
        """
        if key_info.can_execute_tool(tool_name):
            return True, f"Client '{key_info.name}' is authorized for tool '{tool_name}'."
        return False, f"Client '{key_info.name}' is NOT authorized to execute tool '{tool_name}'."


class AuthConfig(BaseModel):
    """Enterprise Authentication configuration parsed via Pydantic v2."""

    enabled: bool = Field(default=False, description="Enable or disable auth layer")
    auth_type: str = Field(default="api_key", alias="type", description="Auth protocol type")
    header_name: str = Field(default="X-API-Key", description="Header name for API key")
    jwt_secret: str = Field(default="enterprise-secret-key", description="JWT secret key")
    jwt_algorithms: List[str] = Field(default_factory=lambda: ["HS256"], description="Allowed JWT algorithms")
    expected_issuer: Optional[str] = Field(default=None, description="Expected authorization server issuer URL for RFC 9207 validation")
    allow_anonymous_paths: List[str] = Field(
        default_factory=lambda: [
            "/",
            "/health",
            "/metrics",
            "/favicon.ico",
            "/scenarios",
            "/scenarios/select",
            "/scenarios/clear",
            "/sessions",
            "/tools",
        ],
        description="Exempt public paths from authentication",
    )
    keys: Dict[str, APIKeyInfo] = Field(
        default_factory=dict, description="Dictionary mapping secret key string to APIKeyInfo model"
    )

    model_config = {
        "populate_by_name": True,
        "extra": "ignore",
    }

    def __init__(self, data: Optional[Dict] = None, **kwargs):
        """Allow initializing AuthConfig with either a dict positional arg or kwargs."""
        if isinstance(data, dict):
            merged = {**data, **kwargs}
            super().__init__(**merged)
        else:
            super().__init__(**kwargs)

    @field_validator("keys", mode="before")
    @classmethod
    def parse_keys_list_or_dict(cls, v) -> Dict[str, APIKeyInfo]:
        """Convert list of key objects or dict from YAML/JSON into a Dict[str, APIKeyInfo]."""
        if isinstance(v, list):
            result = {}
            for item in v:
                if isinstance(item, APIKeyInfo):
                    result[item.key] = item
                elif isinstance(item, dict):
                    info = APIKeyInfo.model_validate(item)
                    result[info.key] = info
            return result
        elif isinstance(v, dict):
            result = {}
            for k, val in v.items():
                if isinstance(val, APIKeyInfo):
                    result[k] = val
                elif isinstance(val, dict):
                    if "key" not in val:
                        val["key"] = k
                    result[k] = APIKeyInfo.model_validate(val)
            return result
        return {}


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Starlette Middleware enforcing Enterprise Security standards for MCP Gateway.
    """

    def __init__(self, app, auth_config: AuthConfig, server_prefix_map: Optional[Dict[str, str]] = None):
        super().__init__(app)
        self.auth_config = auth_config
        self.server_prefix_map = server_prefix_map or {}

    def _get_target_server(self, path: str) -> Optional[str]:
        """Match request URL path to a configured server name."""
        for prefix, server_name in self.server_prefix_map.items():
            if path.startswith(prefix):
                return server_name
        return None

    def _validate_api_key(self, token: str) -> Optional[APIKeyInfo]:
        """Timing-attack-safe API Key validation using hmac.compare_digest."""
        for stored_key, key_info in self.auth_config.keys.items():
            if hmac.compare_digest(token.encode("utf-8"), stored_key.encode("utf-8")):
                return key_info
        return None

    def _validate_jwt(self, token: str) -> Optional[APIKeyInfo]:
        """Validate JWT token claims, signature, and RFC 9207 Issuer (iss) parameter."""
        try:
            payload = jwt.decode(
                token,
                self.auth_config.jwt_secret,
                algorithms=self.auth_config.jwt_algorithms,
            )
            # RFC 9207 Issuer (iss) validation check if expected_issuer is configured
            expected_issuer = getattr(self.auth_config, "expected_issuer", None)
            token_iss = payload.get("iss")
            if expected_issuer and token_iss and token_iss != expected_issuer:
                logger.warning(f"RFC 9207 Issuer mismatch: expected '{expected_issuer}', got '{token_iss}'")
                return None

            client_name = payload.get("sub", payload.get("name", "jwt-client"))
            allowed_servers = payload.get("allowed_servers", ["*"])
            allowed_tools = payload.get("allowed_tools", ["*"])
            return APIKeyInfo(
                name=client_name,
                key=token,
                allowed_servers=allowed_servers,
                allowed_tools=allowed_tools,
            )
        except (jwt.PyJWTError, Exception):
            return None

    async def dispatch(self, request: Request, call_next):
        # 1. Reject Weak Query Parameter Authentication
        if "api_key" in request.query_params or "token" in request.query_params:
            log_audit_event("WEAK_PROTOCOL_REJECTED", request, 400, message="Rejected query param auth")
            return JSONResponse(
                {
                    "error": "Bad Request - Weak Protocol Disabled",
                    "message": (
                        "Query parameter authentication is disabled for security compliance "
                        "(URIs appear in plain text server logs). Please use HTTP Headers "
                        f"('{self.auth_config.header_name}' or 'Authorization: Bearer <token>')."
                    ),
                },
                status_code=400,
            )

        # Skip authentication if auth is disabled
        if not self.auth_config.enabled:
            return await call_next(request)

        # Allow anonymous public paths or management endpoints (/sessions/*, /scenarios/*, /tools/*, /metrics)
        if (
            request.url.path in self.auth_config.allow_anonymous_paths
            or request.url.path.startswith("/sessions")
            or request.url.path.startswith("/scenarios")
            or request.url.path.startswith("/tools")
            or request.url.path.startswith("/metrics")
        ):
            return await call_next(request)

        # 2. Extract Token from Headers
        token: Optional[str] = None
        if self.auth_config.header_name in request.headers:
            token = request.headers[self.auth_config.header_name]
        else:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
            elif auth_header.startswith("ApiKey "):
                token = auth_header[7:].strip()

        if not token:
            log_audit_event("AUTH_MISSING", request, 401, message="Missing credentials header")
            return JSONResponse(
                {
                    "error": "Unauthorized",
                    "message": f"Enterprise authentication required. Provide header '{self.auth_config.header_name}' or 'Authorization: Bearer <token>'",
                },
                status_code=401,
            )

        # 3. Validate Token
        key_info: Optional[APIKeyInfo] = None
        if self.auth_config.auth_type.lower() == "jwt":
            key_info = self._validate_jwt(token)
        else:
            key_info = self._validate_api_key(token)
            if not key_info and self.auth_config.jwt_secret:
                # Fallback to JWT if API Key mismatch
                key_info = self._validate_jwt(token)

        if not key_info:
            log_audit_event("AUTH_FAILURE", request, 403, message="Invalid token or key")
            return JSONResponse(
                {
                    "error": "Forbidden",
                    "message": "Invalid API key, JWT token, or expired credentials.",
                },
                status_code=403,
            )

        # 4. Check Per-Server Access Control
        target_server = self._get_target_server(request.url.path)
        if target_server and not key_info.can_access(target_server):
            log_audit_event(
                "ACCESS_DENIED",
                request,
                403,
                client_name=key_info.name,
                message=f"Access denied to server '{target_server}'",
            )
            return JSONResponse(
                {
                    "error": "Forbidden",
                    "message": f"Client '{key_info.name}' is not authorized to access server '{target_server}'.",
                },
                status_code=403,
            )

        # Attach client info & key info for per-call authorization
        request.state.client_name = key_info.name
        request.state.key_info = key_info
        log_audit_event("AUTH_SUCCESS", request, 200, client_name=key_info.name)

        return await call_next(request)
