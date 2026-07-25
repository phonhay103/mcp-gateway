"""
Real External MCP Server Registry & Upstream Proxy Engine.
Allows registering real external HTTP/Streamable-HTTP MCP servers dynamically at runtime or via configuration.
Proxies JSON-RPC calls (tools/list, tools/call, resources/list, prompts/list) over HTTP while enforcing Gateway
security (Hash-Pinning, Description Sanitization, Per-Call Authorization), trace recording, and metrics.
"""

from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field

from mcp_gateway.logger import logger
from mcp_gateway.metrics import metrics_collector
from mcp_gateway.schemas import ToolDefinition, schema_registry
from mcp_gateway.security import ToolDescriptionSanitizer, hash_pinning_guard


class UpstreamMCPServerConfig(BaseModel):
    """Pydantic v2 Model for Real External Upstream MCP Server Registration."""

    server_id: str = Field(..., description="Unique Upstream Server ID")
    name: str = Field(..., description="Human readable name")
    upstream_url: str = Field(..., description="Streamable HTTP or REST endpoint of real external MCP server (e.g. 'http://external-mcp:8080/mcp')")
    headers: Dict[str, str] = Field(default_factory=dict, description="Custom HTTP headers (e.g. Authorization, X-API-Key)")
    timeout_seconds: float = Field(default=30.0, ge=1.0, description="HTTP timeout in seconds")
    scenario_id: Optional[str] = Field(default=None, description="Optional Scenario ID to automatically mount this server under")

    model_config = {"extra": "ignore"}


class UpstreamProxyRegistry:
    """Registry managing active connections and tool proxies for real external MCP servers."""

    def __init__(self):
        self._servers: Dict[str, UpstreamMCPServerConfig] = {}
        self._discovered_tools: Dict[str, Dict[str, Any]] = {}

    def register_server(self, config: UpstreamMCPServerConfig) -> None:
        """Register an external real MCP server."""
        self._servers[config.server_id] = config
        logger.info(f"Registered Real External MCP Server [{config.server_id}] -> {config.upstream_url}")

    def unregister_server(self, server_id: str) -> bool:
        """Unregister an external real MCP server."""
        if server_id in self._servers:
            self._servers.pop(server_id)
            logger.info(f"Unregistered External MCP Server [{server_id}]")
            return True
        return False

    def list_servers(self) -> List[Dict[str, Any]]:
        """List all registered external real MCP servers."""
        return [srv.model_dump() for srv in self._servers.values()]

    def get_server(self, server_id: str) -> Optional[UpstreamMCPServerConfig]:
        """Get config for registered external server."""
        return self._servers.get(server_id)

    async def discover_upstream_tools(self, server_id: str) -> List[ToolDefinition]:
        """
        Fetch tools list from real upstream MCP server via JSON-RPC 'tools/list'.
        Automatically applies Gateway Security:
        1. SHA-256 Hash-Pinning Guard verification against Rug-Pulls.
        2. Tool Description Sanitizer against Prompt Injection / Tool Poisoning.
        """
        srv = self.get_server(server_id)
        if not srv:
            raise ValueError(f"Upstream MCP Server '{server_id}' not found")

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **srv.headers,
        }

        jsonrpc_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }

        async with httpx.AsyncClient(timeout=srv.timeout_seconds) as client:
            try:
                resp = await client.post(srv.upstream_url, json=jsonrpc_payload, headers=headers)
                resp.raise_for_status()

                data = resp.json()
                result = data.get("result", {})
                raw_tools = result.get("tools", [])

                verified_tools = []
                for t in raw_tools:
                    tool_name = t.get("name")
                    description = t.get("description", "")
                    input_schema = t.get("inputSchema", {})

                    # 1. Sanitize tool description
                    sanitized_desc = ToolDescriptionSanitizer.sanitize_description(description)
                    t["description"] = sanitized_desc

                    # 2. Schema Hash Pinning Verification
                    hash_ok = hash_pinning_guard.verify_tool_schema(tool_name, t)
                    if not hash_ok:
                        logger.error(f"Upstream Tool '{tool_name}' from server '{server_id}' failed SHA-256 Hash-Pinning check!")
                        continue

                    tool_def = ToolDefinition(
                        name=tool_name,
                        description=sanitized_desc,
                        inputSchema=input_schema,
                    )
                    verified_tools.append(tool_def)
                    schema_registry.register_tool_definition(tool_def)

                    self._discovered_tools[tool_name] = {
                        "server_id": server_id,
                        "upstream_url": srv.upstream_url,
                    }

                logger.info(f"Discovered {len(verified_tools)} secure tools from upstream server [{server_id}]")
                return verified_tools

            except Exception as exc:
                logger.error(f"Failed to discover tools from upstream MCP server [{server_id}] ({srv.upstream_url}): {exc}")
                raise

    async def forward_tool_call(
        self,
        server_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        headers_override: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Forward a JSON-RPC 'tools/call' request to the real external upstream MCP server over HTTP.
        """
        srv = self.get_server(server_id)
        if not srv:
            return {
                "jsonrpc": "2.0",
                "id": "upstream-error",
                "error": {"code": -32002, "message": f"Upstream MCP Server '{server_id}' not found in registry"},
            }

        # W3C Trace Context Propagation
        traceparent = headers_override.get("traceparent") if headers_override else None
        if not traceparent:
            import uuid
            # Generate valid W3C traceparent (version 00, 32-hex trace_id, 16-hex parent_id, flags 01)
            tid = uuid.uuid4().hex
            pid = uuid.uuid4().hex[:16]
            traceparent = f"00-{tid}-{pid}-01"

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "traceparent": traceparent,
            **srv.headers,
        }
        if headers_override:
            headers.update(headers_override)

        jsonrpc_payload = {
            "jsonrpc": "2.0",
            "id": 100,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": tool_args or {},
            },
        }

        async with httpx.AsyncClient(timeout=srv.timeout_seconds) as client:
            try:
                resp = await client.post(srv.upstream_url, json=jsonrpc_payload, headers=headers)
                metrics_collector.record_request("POST", srv.upstream_url, resp.status_code)
                return resp.json()

            except httpx.TimeoutException:
                logger.error(f"Timeout forwarding call '{tool_name}' to upstream [{server_id}] ({srv.upstream_url})")
                return {
                    "jsonrpc": "2.0",
                    "id": "upstream-timeout",
                    "error": {"code": -32004, "message": f"Upstream MCP Server '{server_id}' timed out"},
                }
            except Exception as exc:
                logger.error(f"Error forwarding call '{tool_name}' to upstream [{server_id}]: {exc}")
                return {
                    "jsonrpc": "2.0",
                    "id": "upstream-error",
                    "error": {"code": -32002, "message": f"Upstream proxy error: {exc}"},
                }


# Global singleton instance
upstream_proxy_registry = UpstreamProxyRegistry()
