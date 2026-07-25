"""
Pydantic v2 Models for Gateway Configuration, Scenarios, and Base MCP Servers.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator

from mcp_gateway.auth import AuthConfig


class RateLimitConfig(BaseModel):
    """Pydantic v2 Model for Gateway Rate Limiting Configuration."""

    enabled: bool = Field(default=True, description="Enable sliding window rate limiting")
    requests_per_minute: int = Field(default=120, description="Max requests per minute")

    model_config = {"extra": "ignore"}


class GatewayConfig(BaseModel):
    """Pydantic v2 Model for Gateway Global Configuration."""

    host: str = Field(default="0.0.0.0", description="Host address")
    port: int = Field(default=8000, description="Shared gateway port")
    enabled: bool = Field(default=True, description="Enable gateway mode")
    header_session_name: str = Field(default="X-Session-ID", description="Header for session tracking")
    header_scenario_name: str = Field(default="X-MCP-Scenario", description="Header for scenario selection")
    scenarios_dir: str = Field(default="config/scenarios", description="Directory containing scenario YAML files")
    fixtures_dir: str = Field(default="config/fixtures", description="Directory containing fixture YAML files")
    schemas_dir: str = Field(default="config/schemas/tools", description="Directory containing tool schema JSON files")
    allowed_origins: List[str] = Field(default_factory=lambda: ["*"], description="Allowed CORS/DNS Rebinding origins")
    rate_limiting: RateLimitConfig = Field(default_factory=RateLimitConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    model_config = {"extra": "ignore"}


class BaseMCPServerConfig(BaseModel):
    """Pydantic v2 Model for Decoupled Base MCP Server Definitions (Scenario-Agnostic)."""

    id: str = Field(default="", description="Unique MCP Server ID")
    name: str = Field(default="", description="Server Name")
    description: str = Field(default="", description="Server Description")
    tools: List[str] = Field(default_factory=list, description="Base Tools provided by this MCP Server")
    resources: List[str] = Field(default_factory=list, description="Base Resources provided by this MCP Server")
    prompts: List[str] = Field(default_factory=list, description="Base Prompts provided by this MCP Server")

    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def ensure_id_and_name(self) -> "BaseMCPServerConfig":
        if not self.id and self.name:
            self.id = self.name.lower().replace(" ", "-")
        elif not self.name and self.id:
            self.name = self.id
        return self


class ToolErrorConfig(BaseModel):
    """Detailed tool-level error definition conforming to MCP standard (isError: true)."""

    is_error: bool = Field(default=True, description="Enforce MCP standard isError: true on wire protocol")
    message: str = Field(default="Tool execution error defined by scenario", description="Detailed error message")
    code: Optional[int] = Field(default=None, description="Optional error code (e.g. -32603, -32001)")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Optional extra error details/context")

    model_config = {"extra": "ignore"}


class ToolBehaviorConfig(BaseModel):
    """Detailed tool-level configuration defining result overrides or error behavior for a specific tool in a scenario."""

    error: Optional[ToolErrorConfig] = Field(default=None, description="Tool-level error override definition")
    mock_result: Optional[Dict[str, Any]] = Field(default=None, description="Tool-level mock result override")

    model_config = {"extra": "ignore"}


class ErrorSimulationRule(BaseModel):
    """Pydantic v2 model defining an MCP tool error simulation rule for a scenario."""

    tool: str = Field(description="Name of the tool to simulate error for")
    message: str = Field(
        default="Tool execution error simulated by scenario",
        description="Error message text returned to MCP client",
    )
    is_error: bool = Field(
        default=True,
        description="Enforce MCP standard isError: True in CallToolResult payload",
    )

    model_config = {"extra": "ignore"}


class ScenarioConfig(BaseModel):
    """
    Pydantic v2 Model for Individual Scenario Definitions.
    Scenarios dictate EXACTLY which tools, resources, and prompts are loaded.
    """

    id: str = Field(default="", description="Unique Scenario ID")
    name: str = Field(default="", description="Human-readable scenario name")
    description: str = Field(default="", description="Scenario description")
    transport: str = Field(default="streamable-http", description="MCP transport protocol ('streamable-http')")
    prefix: str = Field(default="", description="URL prefix when mounted in Gateway")
    enable_gateway: bool = Field(default=True, description="Mount on shared gateway port")
    servers: List[str] = Field(default_factory=list, description="Referenced Base MCP Server IDs")
    fixtures: List[str] = Field(default_factory=list, description="Referenced Fixture filenames")
    tools: List[str] = Field(default_factory=list, description="Explicit tools selection for this scenario")
    resources: List[str] = Field(default_factory=list, description="Explicit resources selection for this scenario")
    prompts: List[str] = Field(default_factory=list, description="Explicit prompts selection for this scenario")
    tool_behaviors: Dict[str, ToolBehaviorConfig] = Field(
        default_factory=dict,
        description="Detailed tool-level result overrides or MCP error definitions mapped by tool_name",
    )
    error_simulations: List[ErrorSimulationRule] = Field(
        default_factory=list,
        description="List of MCP tool error simulations for this scenario (isError: true)",
    )

    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def ensure_id_and_prefix(self) -> "ScenarioConfig":
        if not self.id and self.name:
            self.id = self.name.lower().replace(" ", "-")
        elif not self.name and self.id:
            self.name = self.id

        if not self.prefix and self.id:
            self.prefix = f"/{self.id.lower().replace(' ', '-')}"
        elif not self.prefix:
            self.prefix = "/scenario"

        if not self.prefix.startswith("/"):
            self.prefix = "/" + self.prefix

        return self


# Backwards compatibility alias for ServerConfig
ServerConfig = ScenarioConfig


class GatewayYamlConfig(BaseModel):
    """Pydantic v2 Root Model for entire `mcp_servers.yaml` configuration."""

    version: str = Field(default="3.0", description="Configuration schema version")
    description: str = Field(default="", description="Configuration file description")
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    servers: List[BaseMCPServerConfig] = Field(default_factory=list, description="Decoupled Base MCP Servers")
    scenarios: List[ScenarioConfig] = Field(default_factory=list, description="Scenarios")

    model_config = {"extra": "ignore"}
