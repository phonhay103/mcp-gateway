"""
Pydantic v2 Models for Gateway Configuration and Adapters.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from mcp_gateway.security.auth import AuthConfig
from mcp_gateway.core.adapters import PlugInAdapterConfig


class RateLimitConfig(BaseModel):
    """Pydantic v2 Model for Gateway Rate Limiting Configuration."""

    enabled: bool = Field(default=True, description="Enable sliding window rate limiting")
    requests_per_minute: int = Field(default=120, description="Max requests per minute")

    model_config = {"extra": "ignore"}


class GatewayConfig(BaseModel):
    """Pydantic v2 Model for Gateway Global Configuration."""

    host: str = Field(default="0.0.0.0", description="Host address")
    port: int = Field(default=2026, description="Shared gateway port")
    allowed_origins: List[str] = Field(default_factory=lambda: ["*"], description="Allowed CORS/DNS Rebinding origins")
    rate_limiting: RateLimitConfig = Field(default_factory=RateLimitConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    model_config = {"extra": "ignore"}


class GatewayYamlConfig(BaseModel):
    """Pydantic v2 Root Model for entire `gateway_config.yaml` configuration."""

    version: str = Field(default="3.0", description="Configuration schema version")
    description: str = Field(default="", description="Configuration file description")
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    adapters: List[PlugInAdapterConfig] = Field(default_factory=list, description="Pre-configured Upstream Adapters")

    model_config = {"extra": "ignore"}

