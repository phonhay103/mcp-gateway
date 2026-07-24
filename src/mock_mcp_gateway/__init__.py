"""Mock MCP package."""

from .core.server import mcp
from .core.manager import ServerManager
from .core.adapters import adapter_registry, PlugInAdapterConfig, AdapterStatus, AdapterType
from .core.proxy import upstream_proxy_registry, UpstreamMCPServerConfig
from .core.chaos import chaos_engine, FaultRule, FaultType
from .core.session import session_manager, SessionData, SessionMiddleware
from .security.auth import AuthConfig, AuthMiddleware, PerCallAuthorizer
from .security.security import EnterpriseSecurityMiddleware, ToolDescriptionSanitizer
from .security.rate_limiter import RateLimitMiddleware
from .observability.logger import logger, setup_logging
from .observability.metrics import metrics_collector
from .observability.recorder import trace_recorder

__all__ = [
    "mcp",
    "ServerManager",
    "adapter_registry",
    "PlugInAdapterConfig",
    "AdapterStatus",
    "AdapterType",
    "upstream_proxy_registry",
    "UpstreamMCPServerConfig",
    "chaos_engine",
    "FaultRule",
    "FaultType",
    "session_manager",
    "SessionData",
    "SessionMiddleware",
    "AuthConfig",
    "AuthMiddleware",
    "PerCallAuthorizer",
    "EnterpriseSecurityMiddleware",
    "ToolDescriptionSanitizer",
    "RateLimitMiddleware",
    "logger",
    "setup_logging",
    "metrics_collector",
    "trace_recorder",
]
