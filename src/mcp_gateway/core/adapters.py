"""
Plug-In / Plug-Out Adapter Architecture for Real and OpenAPI MCP Servers.
Treats all MCP Servers—whether Real Upstream MCP Servers or OpenAPI REST Bridges—
as modular Plug-In Adapters with uniform Control Plane management (/adapters) and Data Plane routing (/adapters/{name}/mcp).
"""

from enum import Enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator

from mcp_gateway.observability import logger, metrics_collector
from mcp_gateway.core.schemas import ToolDefinition, schema_registry


class AdapterType(str, Enum):
    """Supported MCP Adapter Types."""

    REAL_UPSTREAM = "real_upstream"
    OPENAPI_BRIDGE = "openapi_bridge"
    GRAPHQL_BRIDGE = "graphql_bridge"


class AdapterStatus(str, Enum):
    """Adapter Operational Status."""

    RUNNING = "Running"
    PAUSED = "Paused"
    STOPPED = "Stopped"
    FAILED = "Failed"


class PlugInAdapterConfig(BaseModel):
    """Pydantic v2 Model for a Plug-In MCP Server Adapter."""

    name: str = Field(..., description="Unique Adapter Name / ID (e.g. 'sysops-mock', 'external-weather-mcp')")
    description: str = Field(default="", description="Adapter description")
    adapter_type: AdapterType = Field(default=AdapterType.REAL_UPSTREAM, description="Adapter type")
    upstream_url: Optional[str] = Field(default=None, description="HTTP URL for real upstream MCP server")
    openapi_spec_url: Optional[str] = Field(default=None, description="HTTP URL for OpenAPI spec (json/yaml)")
    openapi_spec_path: Optional[str] = Field(default=None, description="Local file path for OpenAPI spec (json/yaml)")
    graphql_schema_url: Optional[str] = Field(default=None, description="HTTP URL for GraphQL schema/introspection")
    graphql_schema_path: Optional[str] = Field(default=None, description="Local file path for GraphQL schema/introspection")
    graphql_schema: Optional[Any] = Field(default=None, description="Inline GraphQL schema SDL or dict")
    base_url: Optional[str] = Field(default=None, description="Base REST API URL for OpenAPI server or GraphQL endpoint")
    headers: Dict[str, str] = Field(default_factory=dict, description="Custom HTTP headers for upstream calls")
    allowed_forward_headers: List[str] = Field(default_factory=list, description="Whitelist of client-supplied headers allowed to be forwarded downstream")
    tools: List[str] = Field(default_factory=list, description="Explicit or discovered tool names provided by this adapter")
    resources: List[str] = Field(default_factory=list, description="Resource URIs provided by this adapter")
    prompts: List[str] = Field(default_factory=list, description="Prompt names provided by this adapter")
    fixtures: List[str] = Field(default_factory=list, description="Fixture files loaded for mock state initialization")
    required_roles: List[str] = Field(default_factory=list, description="RBAC roles required to access this adapter (e.g. ['mcp.admin'])")
    status: AdapterStatus = Field(default=AdapterStatus.RUNNING, description="Current operational status")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    model_config = {"extra": "ignore"}


class AdapterLogEntry(BaseModel):
    """Pydantic v2 Model for an Adapter Runtime Log Entry."""

    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    level: str = "INFO"
    message: str

    model_config = {"extra": "ignore"}


class AdapterRegistry:
    """Control Plane Registry managing the Plug-In / Plug-Out Lifecycle of MCP Adapters."""

    def __init__(self):
        self._adapters: Dict[str, PlugInAdapterConfig] = {}
        self._logs: Dict[str, List[AdapterLogEntry]] = {}

    def log_adapter_event(self, name: str, level: str, message: str) -> None:
        """Append runtime log entry for a specific adapter."""
        if name not in self._logs:
            self._logs[name] = []
        entry = AdapterLogEntry(level=level, message=message)
        self._logs[name].append(entry)
        if len(self._logs[name]) > 500:  # Ring buffer cap
            self._logs[name].pop(0)

    def register_adapter(self, config: PlugInAdapterConfig) -> PlugInAdapterConfig:
        """Plug-in a new MCP server adapter."""
        config.updated_at = datetime.now(timezone.utc).isoformat()
        self._adapters[config.name] = config
        self.log_adapter_event(config.name, "INFO", f"Plugged-in MCP Adapter [{config.name}] type={config.adapter_type.value}")
        logger.info(f"Plugged-in MCP Server Adapter [{config.name}] ({config.adapter_type.value})")
        metrics_collector.record_audit_event("ADAPTER_PLUGGED_IN")
        return config

    def unregister_adapter(self, name: str) -> bool:
        """Plug-out (unregister) an MCP server adapter."""
        if name in self._adapters:
            self._adapters.pop(name)
            self._logs.pop(name, None)
            logger.info(f"Plugged-out MCP Server Adapter [{name}]")
            metrics_collector.record_audit_event("ADAPTER_PLUGGED_OUT")
            return True
        return False

    def get_adapter(self, name: str) -> Optional[PlugInAdapterConfig]:
        """Retrieve adapter configuration."""
        return self._adapters.get(name)

    def list_adapters(self, role_filter: Optional[List[str]] = None) -> List[PlugInAdapterConfig]:
        """List all plugged-in adapters, with optional RBAC role filtering."""
        result = []
        for adapter in self._adapters.values():
            if not adapter.required_roles or not role_filter:
                result.append(adapter)
            else:
                if any(r in adapter.required_roles or r == "mcp.admin" for r in role_filter):
                    result.append(adapter)
        return result

    def get_adapter_status(self, name: str) -> Dict[str, Any]:
        """Get operational status and tool metrics for an adapter."""
        adapter = self.get_adapter(name)
        if not adapter:
            return {"status": "NotFound", "error": f"Adapter '{name}' not found"}

        return {
            "name": adapter.name,
            "status": adapter.status.value,
            "type": adapter.adapter_type.value,
            "upstream_url": adapter.upstream_url,
            "tools_count": len(adapter.tools),
            "updated_at": adapter.updated_at,
        }

    def get_adapter_logs(self, name: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve runtime log entries for an adapter."""
        logs = self._logs.get(name, [])
        return [l.model_dump() for l in logs[-limit:]]

    def update_adapter_status(self, name: str, status: AdapterStatus, reason: str = "") -> bool:
        """Update operational status of an adapter."""
        adapter = self.get_adapter(name)
        if not adapter:
            return False

        adapter.status = status
        adapter.updated_at = datetime.now(timezone.utc).isoformat()
        self.log_adapter_event(name, "WARNING" if status != AdapterStatus.RUNNING else "INFO", f"Status changed to {status.value}: {reason}")
        logger.info(f"Adapter [{name}] status updated to {status.value}")
        return True


# Global singleton instance
adapter_registry = AdapterRegistry()
