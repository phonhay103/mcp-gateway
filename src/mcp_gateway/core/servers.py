"""
Core Server Management Architecture for Enterprise MCP Gateway.
Manages all connected MCP Services—whether Upstream MCP Servers, OpenAPI Servers, or GraphQL Servers—
as native Core Gateway Services.
"""

from enum import Enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, AliasChoices

from mcp_gateway.observability import logger, metrics_collector
from mcp_gateway.core.schemas import ToolDefinition, schema_registry


class ServerType(str, Enum):
    """Supported Core Gateway Server Types."""

    MCP = "mcp"
    REAL_UPSTREAM = "real_upstream"
    OPENAPI_BRIDGE = "openapi_bridge"
    GRAPHQL_BRIDGE = "graphql_bridge"


class ServerStatus(str, Enum):
    """Server Operational Status."""

    RUNNING = "Running"
    PAUSED = "Paused"
    STOPPED = "Stopped"
    FAILED = "Failed"


class ServerConfig(BaseModel):
    """Pydantic v2 Model for a Core Gateway Registered Server."""

    server_id: str = Field(..., validation_alias=AliasChoices("server_id", "id", "name"), description="Unique Server ID / Name (e.g. 'sysops-mock', 'external-weather-mcp')")
    name: Optional[str] = Field(default=None, description="Human-readable server name")
    description: str = Field(default="", description="Server description")
    server_type: ServerType = Field(default=ServerType.MCP, description="Server protocol type")
    upstream_url: Optional[str] = Field(default=None, description="HTTP URL for real upstream MCP server")
    openapi_spec_url: Optional[str] = Field(default=None, description="HTTP URL for OpenAPI spec (json/yaml)")
    openapi_spec_path: Optional[str] = Field(default=None, description="Local file path for OpenAPI spec (json/yaml)")
    graphql_schema_url: Optional[str] = Field(default=None, description="HTTP URL for GraphQL schema/introspection")
    graphql_schema_path: Optional[str] = Field(default=None, description="Local file path for GraphQL schema/introspection")
    graphql_schema: Optional[Any] = Field(default=None, description="Inline GraphQL schema SDL or dict")
    base_url: Optional[str] = Field(default=None, description="Base REST API URL for OpenAPI server or GraphQL endpoint")
    headers: Dict[str, str] = Field(default_factory=dict, description="Custom HTTP headers for upstream calls")
    allowed_forward_headers: List[str] = Field(default_factory=list, description="Whitelist of client-supplied headers allowed to be forwarded downstream")
    tools: List[str] = Field(default_factory=list, description="Explicit or discovered tool names provided by this server")
    resources: List[str] = Field(default_factory=list, description="Resource URIs provided by this server")
    prompts: List[str] = Field(default_factory=list, description="Prompt names provided by this server")
    fixtures: List[str] = Field(default_factory=list, description="Fixture files loaded for mock state initialization")
    required_roles: List[str] = Field(default_factory=list, description="RBAC roles required to access this server")
    status: ServerStatus = Field(default=ServerStatus.RUNNING, description="Current operational status")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    model_config = {"extra": "ignore"}

    @property
    def id(self) -> str:
        return self.server_id


class ServerLogEntry(BaseModel):
    """Pydantic v2 Model for a Server Runtime Log Entry."""

    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    level: str = "INFO"
    message: str

    model_config = {"extra": "ignore"}


class ServerRegistry:
    """Core Server Registry managing all connected MCP Services."""

    def __init__(self):
        self._servers: Dict[str, ServerConfig] = {}
        self._logs: Dict[str, List[ServerLogEntry]] = {}

    def log_server_event(self, server_id: str, level: str, message: str) -> None:
        """Append runtime log entry for a specific server."""
        if server_id not in self._logs:
            self._logs[server_id] = []
        entry = ServerLogEntry(level=level, message=message)
        self._logs[server_id].append(entry)
        if len(self._logs[server_id]) > 500:
            self._logs[server_id].pop(0)

    def register_server(self, config: ServerConfig) -> ServerConfig:
        """Register a core gateway server."""
        config.updated_at = datetime.now(timezone.utc).isoformat()
        self._servers[config.server_id] = config
        self.log_server_event(config.server_id, "INFO", f"Registered Core Server [{config.server_id}] type={config.server_type.value}")
        logger.info(f"Registered Core MCP Server [{config.server_id}] ({config.server_type.value})")
        metrics_collector.record_audit_event("SERVER_REGISTERED")
        return config

    def unregister_server(self, server_id: str) -> bool:
        """Unregister a core gateway server."""
        if server_id in self._servers:
            self._servers.pop(server_id)
            self._logs.pop(server_id, None)
            logger.info(f"Unregistered Core Server [{server_id}]")
            metrics_collector.record_audit_event("SERVER_UNREGISTERED")
            return True
        return False

    def get_server(self, server_id: str) -> Optional[ServerConfig]:
        """Retrieve server configuration."""
        return self._servers.get(server_id)

    def list_servers(self, role_filter: Optional[List[str]] = None) -> List[ServerConfig]:
        """List all registered servers, with optional RBAC role filtering."""
        result = []
        for server in self._servers.values():
            if not server.required_roles or not role_filter:
                result.append(server)
            else:
                if any(r in server.required_roles or r == "mcp.admin" for r in role_filter):
                    result.append(server)
        return result

    def get_server_status(self, server_id: str) -> Dict[str, Any]:
        """Get operational status and telemetry for a server."""
        srv = self.get_server(server_id)
        if not srv:
            return {"status": "NotFound", "error": f"Server '{server_id}' is not registered"}

        return {
            "server_id": srv.server_id,
            "status": srv.status.value,
            "server_type": srv.server_type.value,
            "tools_count": len(srv.tools),
            "updated_at": srv.updated_at,
        }

    def get_server_logs(self, server_id: str) -> List[Dict[str, Any]]:
        """Get runtime log history for a server."""
        return [l.model_dump() for l in self._logs.get(server_id, [])]

    def update_server_status(self, server_id: str, new_status: ServerStatus, reason: str = "") -> bool:
        """Update operational status for a server."""
        srv = self.get_server(server_id)
        if srv:
            srv.status = new_status
            srv.updated_at = datetime.now(timezone.utc).isoformat()
            self.log_server_event(server_id, "WARNING" if new_status != ServerStatus.RUNNING else "INFO", f"Status changed to {new_status.value}. Reason: {reason}")
            logger.info(f"Server [{server_id}] status updated -> {new_status.value}")
            return True
        return False


server_registry = ServerRegistry()
