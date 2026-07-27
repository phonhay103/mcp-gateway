"""
REST API Endpoint Handlers for Gateway Control Plane & Management APIs.
Includes unified /servers, /metrics, and /mcp routes.
"""

from typing import Any, Dict, List
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mcp_gateway.core.servers import ServerStatus, ServerConfig, server_registry
from mcp_gateway.observability import logger
from mcp_gateway.observability.metrics import metrics_collector
from mcp_gateway.core.proxy import UpstreamMCPServerConfig, upstream_proxy_registry
from mcp_gateway.core.openapi_bridge import OpenAPIBridgeConfig, openapi_bridge_registry
from mcp_gateway.core.graphql_bridge import GraphQLBridgeConfig, graphql_bridge_registry
from mcp_gateway.core.router import ToolGatewayRouter
from mcp_gateway.security.security import extract_forwardable_headers
from mcp_gateway.core.schemas import schema_registry


def create_gateway_routes() -> List[Route]:
    """Factory creating Starlette Route definitions for the Gateway."""

    async def homepage(request):
        session_id = getattr(request.state, "session_id", "N/A")
        metrics_collector.record_request("GET", "/", 200)
        status_data = {
            "status": "online",
            "mode": "enterprise-gateway",
            "session_id": session_id,
            "endpoints": {
                "metrics": "/metrics",
                "global_router": "/mcp",
                "list_servers": "/servers",
                "register_server": "/servers/register",
                "get_server": "/servers/{server_id}",
                "get_server_status": "/servers/{server_id}/status",
                "get_server_logs": "/servers/{server_id}/logs",
                "get_server_tools": "/servers/{server_id}/tools",
                "update_server": "/servers/{server_id}",
                "unregister_server": "/servers/{server_id}",
            },
        }
        return JSONResponse(status_data)

    async def metrics_endpoint(request):
        """GET /metrics: Prometheus & OpenTelemetry compatible metrics endpoint."""
        content = metrics_collector.generate_prometheus_format(active_sessions_count=0)
        return Response(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")

    # --- Unified External Server Control Plane APIs (/servers) ---

    async def list_servers_endpoint(request):
        external_mcp_servers = upstream_proxy_registry.list_servers()
        openapi_servers = openapi_bridge_registry.list_servers()
        graphql_servers = graphql_bridge_registry.list_servers()
        return JSONResponse({
            "status": "success",
            "external_upstream_servers": external_mcp_servers,
            "openapi_bridge_servers": openapi_servers,
            "graphql_bridge_servers": graphql_servers,
        })

    async def register_server_endpoint(request):
        try:
            data = await request.json()
            server_type = data.get("server_type", "mcp").lower()
            client_headers = extract_forwardable_headers(request)

            if server_type == "graphql" or "graphql_schema_url" in data or "graphql_schema" in data or "graphql_schema_path" in data:
                gql_config = GraphQLBridgeConfig.model_validate(data)
                tools = await graphql_bridge_registry.register_graphql_server(gql_config, headers_override=client_headers)
                return JSONResponse({
                    "status": "registered",
                    "server_id": gql_config.server_id,
                    "server_type": "graphql",
                    "base_url": gql_config.base_url,
                    "discovered_tools_count": len(tools),
                }, status_code=201)

            if server_type == "openapi" or "openapi_spec_url" in data or "openapi_spec" in data:
                bridge_config = OpenAPIBridgeConfig.model_validate(data)
                tools = await openapi_bridge_registry.register_openapi_server(bridge_config, headers_override=client_headers)
                return JSONResponse({
                    "status": "registered",
                    "server_id": bridge_config.server_id,
                    "server_type": "openapi",
                    "base_url": bridge_config.base_url,
                    "discovered_tools_count": len(tools),
                }, status_code=201)

            config = UpstreamMCPServerConfig.model_validate(data)
            upstream_proxy_registry.register_server(config)

            discovered_count = 0
            try:
                tools = await upstream_proxy_registry.discover_upstream_tools(config.server_id, headers_override=client_headers)
                discovered_count = len(tools)
            except Exception as exc:
                logger.warning(f"Registered external server [{config.server_id}] but tools auto-discovery pending: {exc}")

            return JSONResponse({
                "status": "registered",
                "server_id": config.server_id,
                "server_type": "mcp",
                "upstream_url": config.upstream_url,
                "discovered_tools_count": discovered_count,
            }, status_code=201)
        except Exception as exc:
            return JSONResponse({"error": "Bad Request", "message": f"Failed to register server: {exc}"}, status_code=400)

    async def get_server_endpoint(request):
        server_id = request.path_params.get("server_id")
        srv = server_registry.get_server(server_id)
        if not srv:
            return JSONResponse({"error": "Not Found", "message": f"Server '{server_id}' not found"}, status_code=404)
        return JSONResponse(srv.model_dump())

    async def get_server_status_endpoint(request):
        server_id = request.path_params.get("server_id")
        status_data = server_registry.get_server_status(server_id)
        if status_data.get("status") == "NotFound":
            return JSONResponse({"error": "Not Found", "message": status_data.get("error")}, status_code=404)
        return JSONResponse(status_data)

    async def get_server_logs_endpoint(request):
        server_id = request.path_params.get("server_id")
        logs = server_registry.get_server_logs(server_id)
        return JSONResponse({"status": "success", "server_id": server_id, "count": len(logs), "logs": logs})

    async def get_server_tools_endpoint(request):
        server_id = request.path_params.get("server_id")
        srv = server_registry.get_server(server_id)
        if not srv:
            return JSONResponse({"error": "Not Found", "message": f"Server '{server_id}' not found"}, status_code=404)
        
        tools_list = []
        for t_name in srv.tools:
            t_def = schema_registry.get_tool(t_name)
            if t_def:
                tools_list.append(t_def.to_mcp_format())
            else:
                tools_list.append({"name": t_name})
        return JSONResponse({"status": "success", "server_id": server_id, "count": len(tools_list), "tools": tools_list})

    async def update_server_endpoint(request):
        server_id = request.path_params.get("server_id")
        srv = server_registry.get_server(server_id)
        if not srv:
            return JSONResponse({"error": "Not Found", "message": f"Server '{server_id}' not found"}, status_code=404)

        try:
            data = await request.json()
            if "status" in data:
                server_registry.update_server_status(server_id, ServerStatus(data["status"]), reason=data.get("reason", ""))
            if "description" in data:
                srv.description = data["description"]
            if "tools" in data and isinstance(data["tools"], list):
                srv.tools = data["tools"]
            return JSONResponse({"status": "updated", "server": srv.model_dump()})
        except Exception as exc:
            return JSONResponse({"error": "Bad Request", "message": f"Failed to update server: {exc}"}, status_code=400)

    async def unregister_server_endpoint(request):
        server_id = request.path_params.get("server_id")
        removed = upstream_proxy_registry.unregister_server(server_id)
        if not removed:
            removed = openapi_bridge_registry.unregister_server(server_id)
        if not removed:
            removed = graphql_bridge_registry.unregister_server(server_id)
        if not removed:
            removed = server_registry.unregister_server(server_id)
        if not removed:
            return JSONResponse({"error": "Not Found", "message": f"Server '{server_id}' not found"}, status_code=404)
        return JSONResponse({"status": "unregistered", "server_id": server_id})

    async def global_mcp_router_endpoint(request):
        return await ToolGatewayRouter.handle_mcp_request(request)

    return [
        Route("/", homepage, methods=["GET"]),
        Route("/mcp", global_mcp_router_endpoint, methods=["POST"]),
        
        # --- Single Unified External Server Management APIs (/servers) ---
        Route("/servers", list_servers_endpoint, methods=["GET"]),
        Route("/servers/register", register_server_endpoint, methods=["POST"]),
        Route("/servers/{server_id}", get_server_endpoint, methods=["GET"]),
        Route("/servers/{server_id}/status", get_server_status_endpoint, methods=["GET"]),
        Route("/servers/{server_id}/logs", get_server_logs_endpoint, methods=["GET"]),
        Route("/servers/{server_id}/tools", get_server_tools_endpoint, methods=["GET"]),
        Route("/servers/{server_id}", update_server_endpoint, methods=["PUT"]),
        Route("/servers/{server_id}", unregister_server_endpoint, methods=["DELETE"]),

        Route("/metrics", metrics_endpoint, methods=["GET"]),
    ]
