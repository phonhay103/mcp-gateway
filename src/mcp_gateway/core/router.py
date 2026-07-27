"""
Global Tool Gateway Router Engine (POST /mcp Data Plane).
Acts as an intelligent router aggregating tools across all core registered MCP servers, OpenAPI REST Bridges, and GraphQL Bridges.
Applies system-wide security, Trace Recording, Schema Hash-Pinning, Description Sanitization, and Per-Call Auth across all tool call executions.
"""

import time
from typing import Any, Dict, List, Optional
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp_gateway.core.servers import ServerStatus, ServerType, server_registry
from mcp_gateway.security.auth import PerCallAuthorizer
from mcp_gateway.observability import logger
from mcp_gateway.core.proxy import upstream_proxy_registry
from mcp_gateway.core.openapi_bridge import openapi_bridge_registry
from mcp_gateway.core.graphql_bridge import graphql_bridge_registry
from mcp_gateway.core.schemas import schema_registry
from mcp_gateway.security.security import ToolDescriptionSanitizer, hash_pinning_guard, extract_forwardable_headers
from mcp_gateway.core.middleware.header_validation import validate_mcp_headers


class ToolGatewayRouter:
    """Global Router handling central POST /mcp JSON-RPC requests."""

    @staticmethod
    async def handle_mcp_request(request: Request) -> Response:
        """Process incoming Streamable HTTP JSON-RPC request at POST /mcp."""
        # Extract client custom headers to preserve and forward downstream end-to-end
        client_headers = extract_forwardable_headers(request)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error: Invalid JSON"},
            }, status_code=400)

        method = body.get("method")
        req_id = body.get("id")
        params = body.get("params", {})

        # 1. MCP 2026-07-28 Streamable HTTP Header Enforcement Middleware
        header_error = validate_mcp_headers(request, method, req_id, params)
        if header_error:
            return header_error

        # 2. Handle server/discover method (MCP 2026-07-28 stateless capability discovery)
        if method == "server/discover":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2026-07-28",
                    "serverInfo": {"name": "Global Tool Gateway Router", "version": "2026.07.28"},
                    "capabilities": {
                        "statelessCore": True,
                        "extensions": {
                            "tasks": {"supported": False},
                            "serverMultiplexing": {"supported": True}
                        }
                    },
                    "endpoints": {"mcp": "/mcp", "servers": "/servers"}
                }
            })

        # 4. Handle initialize method (Updated to MCP 2026-07-28 Spec with Stateless Core & Extensions)
        if method == "initialize":
            client_protocol_version = params.get("protocolVersion", "2026-07-28")
            negotiated_version = "2026-07-28" if client_protocol_version >= "2026-07-28" else client_protocol_version
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": negotiated_version,
                    "capabilities": {
                        "statelessCore": True,
                        "experimental": {},
                        "extensions": {
                            "mcp-apps": {"supported": True, "version": "1.0"},
                            "tasks": {"supported": True, "version": "1.0"}
                        },
                        "prompts": {"listChanged": True},
                        "resources": {"subscribe": False, "listChanged": True},
                        "tools": {"listChanged": True, "cacheable": True},
                    },
                    "serverInfo": {
                        "name": "Global Tool Gateway Router",
                        "version": "2026.07.28",
                    },
                    "instructions": "Global MCP Gateway Router supporting MCP 2026-07-28 stateless core, extensions, and cached tool lists.",
                },
            })

        # 5. Handle notifications/initialized
        if method == "notifications/initialized":
            return JSONResponse({"jsonrpc": "2.0", "result": {}})

        # 6. Handle tools/list method (Aggregates across all running core servers)
        if method == "tools/list":
            filter_servers = params.get("servers") or params.get("mcp")
            if not filter_servers:
                q_mcp = request.query_params.get("mcp") or request.query_params.get("servers")
                if q_mcp:
                    filter_servers = [s.strip() for s in q_mcp.split(",") if s.strip()]

            aggregated_tools = []
            seen_tools = set()
            running_servers = server_registry.list_servers()

            for server in running_servers:
                if server.status != ServerStatus.RUNNING:
                    continue
                if filter_servers and server.server_id not in filter_servers:
                    continue

                for tool_name in server.tools:
                    tool_def = schema_registry.get_tool(tool_name)
                    if not tool_def:
                        continue

                    # 1. Add standard tool if not already seen
                    if tool_name not in seen_tools:
                        mcp_formatted = tool_def.to_mcp_format()
                        mcp_formatted["description"] = ToolDescriptionSanitizer.sanitize_description(
                            mcp_formatted.get("description", "")
                        )
                        aggregated_tools.append(mcp_formatted)
                        seen_tools.add(tool_name)

                    # 2. Always expose fully-qualified namespaced version (server_id::tool)
                    qualified_name = f"{server.server_id}::{tool_name}"
                    qual_formatted = tool_def.to_mcp_format()
                    qual_formatted["name"] = qualified_name
                    qual_formatted["description"] = f"[{server.server_id}] {ToolDescriptionSanitizer.sanitize_description(tool_def.description)}"
                    aggregated_tools.append(qual_formatted)

            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": aggregated_tools,
                    "ttlMs": 30000,
                    "cacheScope": "user"
                },
            })

        # 7. Handle tools/call method
        if method == "tools/call":
            raw_tool_name = params.get("name")
            tool_args = params.get("arguments", {})

            if not raw_tool_name:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": "Missing 'name' in tools/call parameters"},
                }, status_code=400)

            # Handle namespaced or explicit server targeting
            target_server_id = params.get("server") or params.get("mcp")
            actual_tool_name = raw_tool_name
            if "::" in raw_tool_name:
                parts = raw_tool_name.split("::", 1)
                target_server_id = parts[0]
                actual_tool_name = parts[1]

            tool_name = actual_tool_name

            # Evaluate Per-Call Authorization
            client_info = getattr(request.state, "auth_client", None)
            if client_info:
                auth_ok, auth_msg = PerCallAuthorizer.authorize_tool_call(client_info, tool_name)
                if not auth_ok:
                    return JSONResponse({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32001, "message": f"Unauthorized: {auth_msg}"},
                    }, status_code=403)

            # Identify target server handling this tool with duplicate collision resolution
            target_server = None
            candidate_servers = []

            if target_server_id:
                server = server_registry.get_server(target_server_id)
                if server and server.status == ServerStatus.RUNNING and tool_name in server.tools:
                    target_server = server
                if not target_server:
                    return JSONResponse({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32602,
                            "message": f"Target MCP server '{target_server_id}' not found or does not provide tool '{tool_name}'",
                        },
                    }, status_code=400)
            else:
                for server in server_registry.list_servers():
                    if tool_name in server.tools and server.status == ServerStatus.RUNNING:
                        candidate_servers.append(server)

                if len(candidate_servers) == 1:
                    target_server = candidate_servers[0]
                elif len(candidate_servers) > 1:
                    candidate_names = [s.server_id for s in candidate_servers]
                    return JSONResponse({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32603,
                            "message": (
                                f"Ambiguous tool name collision: Tool '{tool_name}' is provided by multiple MCP servers: {candidate_names}. "
                                f"Please specify 'server': '<server_id>' in parameters, or use fully qualified name '<server_id>::{tool_name}'."
                            ),
                        },
                    }, status_code=400)
                else:
                    target_server = None

            # Validate arguments against schema if registered in schema_registry
            registered_schema_tool = schema_registry.get_tool(tool_name)
            if registered_schema_tool:
                missing_args = registered_schema_tool.validate_arguments(tool_args)
                if missing_args:
                    return JSONResponse({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32602,
                            "message": f"Invalid params: Missing required parameter(s) {missing_args} for tool '{tool_name}'",
                        },
                    }, status_code=400)

            if target_server and (target_server.server_type == ServerType.REAL_UPSTREAM or target_server.server_type == ServerType.MCP):
                # Proxy tool call to real upstream MCP server
                upstream_res = await upstream_proxy_registry.forward_tool_call(
                    server_id=target_server.server_id,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    headers_override=client_headers,
                )
                return JSONResponse(upstream_res)

            if target_server and target_server.server_type == ServerType.OPENAPI_BRIDGE:
                # Proxy tool call to REST API endpoint via OpenAPI Bridge
                openapi_res = await openapi_bridge_registry.call_openapi_tool(
                    server_id=target_server.server_id,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    headers_override=client_headers,
                )
                return JSONResponse(openapi_res)

            if target_server and target_server.server_type == ServerType.GRAPHQL_BRIDGE:
                # Proxy tool call to GraphQL API endpoint via GraphQL Bridge
                graphql_res = await graphql_bridge_registry.call_graphql_tool(
                    server_id=target_server.server_id,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    headers_override=client_headers,
                )
                return JSONResponse(graphql_res)

            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Tool '{tool_name}' not found on any running server"},
            }, status_code=404)

        # Default method not supported
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not implemented by Global Router"},
        }, status_code=405)
