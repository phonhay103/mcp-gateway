"""
Core Package (Minimal exports to avoid circular dependencies).
"""

# Only export essential factories and registries that don't import manager/router
from mcp_gateway.core.server import create_mcp_server, mcp
from mcp_gateway.core.session import session_manager
from mcp_gateway.core.adapters import adapter_registry

__all__ = [
    "create_mcp_server",
    "mcp",
    "session_manager",
    "adapter_registry",
]
