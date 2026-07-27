"""
Pydantic v2 Models for Gateway Configuration, Scenarios, and Base MCP Servers.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator

class AuthConfig(BaseModel):
    """Pydantic v2 Model for Mock Service Auth Config."""

    enabled: bool = Field(default=False)
    header_name: str = Field(default="X-API-Key")

    model_config = {"extra": "ignore"}


class RateLimitConfig(BaseModel):
    """Pydantic v2 Model for Gateway Rate Limiting Configuration."""

    enabled: bool = Field(default=True, description="Enable sliding window rate limiting")
    requests_per_minute: int = Field(default=120, description="Max requests per minute")

    model_config = {"extra": "ignore"}


class GatewayConfig(BaseModel):
    """Pydantic v2 Model for Gateway Global Configuration."""

    host: str = Field(default="0.0.0.0", description="Host address")
    port: int = Field(default=2026, description="Shared gateway port")
    enabled: bool = Field(default=True, description="Enable gateway mode")
    header_session_name: str = Field(default="X-Session-ID", description="Header for session tracking")
    header_scenario_name: str = Field(default="X-MCP-Scenario", description="Header for scenario selection")
    scenarios_dir: str = Field(default="src/mock_mcp/config/scenarios", description="Directory containing scenario YAML files")
    fixtures_dir: str = Field(default="src/mock_mcp/config/fixtures", description="Directory containing fixture YAML files")
    schemas_dir: str = Field(default="src/mock_mcp/config/schemas/tools", description="Directory containing tool schema JSON files")
    allowed_origins: List[str] = Field(default_factory=lambda: ["*"], description="Allowed CORS/DNS Rebinding origins")
    rate_limiting: RateLimitConfig = Field(default_factory=RateLimitConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    model_config = {"extra": "ignore"}




class ToolErrorConfig(BaseModel):
    """Detailed tool-level error definition conforming to MCP standard (isError: true)."""

    is_error: bool = Field(default=True, description="Enforce MCP standard isError: true on wire protocol")
    message: str = Field(default="Tool execution error defined by scenario", description="Detailed error message")
    code: Optional[int] = Field(default=None, description="Optional error code (e.g. -32603, -32001)")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Optional extra error details/context")

    model_config = {"extra": "ignore"}


class ChaosConfig(BaseModel):
    """Pydantic v2 Model for Chaos Fault Injection Configuration inside a Scenario."""

    latency_ms: int = Field(default=0, description="Delay tool execution by fixed milliseconds")
    error_rate: float = Field(default=0.0, description="Rate of random tool failure (0.0 to 1.0)")
    http_status: Optional[int] = Field(default=None, description="Simulate specific HTTP error statuses e.g. 429, 502, 503")

    model_config = {"extra": "ignore"}


class ToolBehaviorConfig(BaseModel):
    """Detailed tool-level configuration defining result overrides or error behavior for a specific tool in a scenario."""

    error: Optional[ToolErrorConfig] = Field(default=None, description="Tool-level error override definition")
    mock_result: Optional[Dict[str, Any]] = Field(default=None, description="Tool-level mock result override")
    chaos: Optional[ChaosConfig] = Field(default=None, description="Tool-level chaos fault injection config")

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
    fixtures: List[str] = Field(default_factory=list, description="Referenced Fixture filenames")
    tools: List[str] = Field(default_factory=list, description="Explicit tools selection for this scenario")
    resources: List[str] = Field(default_factory=list, description="Explicit resources selection for this scenario")
    prompts: List[str] = Field(default_factory=list, description="Explicit prompts selection for this scenario")
    tool_behaviors: Dict[str, ToolBehaviorConfig] = Field(
        default_factory=dict,
        description="Detailed tool-level result overrides or MCP error definitions mapped by tool_name",
    )

    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def ensure_id(self) -> "ScenarioConfig":
        if not self.id and self.name:
            self.id = self.name.lower().replace(" ", "-")
        elif not self.name and self.id:
            self.name = self.id
        return self




class GatewayYamlConfig(BaseModel):
    """Pydantic v2 Root Model for entire `mcp_servers.yaml` configuration."""

    version: str = Field(default="3.0", description="Configuration schema version")
    description: str = Field(default="", description="Configuration file description")
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    scenarios: List[ScenarioConfig] = Field(default_factory=list, description="Scenarios")

    model_config = {"extra": "ignore"}
