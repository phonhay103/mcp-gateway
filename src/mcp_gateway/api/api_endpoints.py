"""
REST API Endpoint Handlers for Gateway Control Plane & Management APIs.
Includes /adapters, /servers, /metrics, and /mcp routes.
"""

from typing import Any, Dict, List
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mcp_gateway.core.adapters import AdapterStatus, PlugInAdapterConfig, adapter_registry
from mcp_gateway.logger import logger
from mcp_gateway.observability.metrics import metrics_collector
from mcp_gateway.core.proxy import UpstreamMCPServerConfig, upstream_proxy_registry
from mcp_gateway.core.openapi_bridge import OpenAPIBridgeConfig, openapi_bridge_registry
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
                "list_adapters": "/adapters",
                "create_adapter": "/adapters",
                "get_adapter": "/adapters/{name}",
                "get_adapter_status": "/adapters/{name}/status",
                "get_adapter_logs": "/adapters/{name}/logs",
                "register_server": "/servers/register",
                "list_servers": "/servers",
                "unregister_server": "/servers/{server_id}",
            },
        }
        return JSONResponse(status_data)

    async def metrics_endpoint(request):
        """GET /metrics: Prometheus & OpenTelemetry compatible metrics endpoint."""
        content = metrics_collector.generate_prometheus_format(active_sessions_count=0)
        return Response(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")

    # --- Adapters Control Plane APIs ---

    async def list_adapters_endpoint(request):
        adapters = adapter_registry.list_adapters()
        return JSONResponse({
            "status": "success",
            "count": len(adapters),
            "adapters": [a.model_dump() for a in adapters],
        })

    async def create_adapter_endpoint(request):
        try:
            data = await request.json()
            config = PlugInAdapterConfig.model_validate(data)
            client_headers = extract_forwardable_headers(request)

            if config.adapter_type == AdapterStatus.RUNNING and (
                data.get("adapter_type") == "openapi_bridge"
                or config.adapter_type == "openapi_bridge"
                or data.get("openapi_spec_url")
                or data.get("openapi_spec")
            ):
                bridge_cfg = OpenAPIBridgeConfig(
                    server_id=config.name,
                    name=config.description or config.name,
                    base_url=config.base_url or "http://localhost:8080",
                    openapi_spec_url=data.get("openapi_spec_url"),
                    openapi_spec_path=data.get("openapi_spec_path"),
                    openapi_spec=data.get("openapi_spec"),
                    headers=config.headers,
                )
                tools = await openapi_bridge_registry.register_openapi_server(bridge_cfg, headers_override=client_headers)
                adapter = adapter_registry.get_adapter(config.name)
                return JSONResponse({"status": "plugged_in", "adapter": adapter.model_dump() if adapter else config.model_dump(), "tools_count": len(tools)}, status_code=201)

            registered = adapter_registry.register_adapter(config)
            return JSONResponse({"status": "plugged_in", "adapter": registered.model_dump()}, status_code=201)
        except Exception as exc:
            return JSONResponse({"error": "Bad Request", "message": f"Failed to plug-in adapter: {exc}"}, status_code=400)

    async def get_adapter_endpoint(request):
        name = request.path_params.get("name")
        adapter = adapter_registry.get_adapter(name)
        if not adapter:
            return JSONResponse({"error": "Not Found", "message": f"Adapter '{name}' not found"}, status_code=404)
        return JSONResponse(adapter.model_dump())

    async def get_adapter_status_endpoint(request):
        name = request.path_params.get("name")
        status_data = adapter_registry.get_adapter_status(name)
        if status_data.get("status") == "NotFound":
            return JSONResponse({"error": "Not Found", "message": status_data.get("error")}, status_code=404)
        return JSONResponse(status_data)

    async def get_adapter_logs_endpoint(request):
        name = request.path_params.get("name")
        logs = adapter_registry.get_adapter_logs(name)
        return JSONResponse({"status": "success", "adapter_name": name, "count": len(logs), "logs": logs})

    async def get_adapter_tools_endpoint(request):
        name = request.path_params.get("name")
        adapter = adapter_registry.get_adapter(name)
        if not adapter:
            return JSONResponse({"error": "Not Found", "message": f"Adapter '{name}' not found"}, status_code=404)
        
        tools_list = []
        for t_name in adapter.tools:
            t_def = schema_registry.get_tool(t_name)
            if t_def:
                tools_list.append(t_def.to_mcp_format())
            else:
                tools_list.append({"name": t_name})
        return JSONResponse({"status": "success", "adapter_name": name, "count": len(tools_list), "tools": tools_list})

    async def update_adapter_endpoint(request):
        name = request.path_params.get("name")
        adapter = adapter_registry.get_adapter(name)
        if not adapter:
            return JSONResponse({"error": "Not Found", "message": f"Adapter '{name}' not found"}, status_code=404)

        try:
            data = await request.json()
            if "status" in data:
                adapter_registry.update_adapter_status(name, AdapterStatus(data["status"]), reason=data.get("reason", ""))
            if "description" in data:
                adapter.description = data["description"]
            if "tools" in data and isinstance(data["tools"], list):
                adapter.tools = data["tools"]
            return JSONResponse({"status": "updated", "adapter": adapter.model_dump()})
        except Exception as exc:
            return JSONResponse({"error": "Bad Request", "message": f"Failed to update adapter: {exc}"}, status_code=400)

    async def delete_adapter_endpoint(request):
        name = request.path_params.get("name")
        unregistered = adapter_registry.unregister_adapter(name)
        if not unregistered:
            return JSONResponse({"error": "Not Found", "message": f"Adapter '{name}' not found"}, status_code=404)
        return JSONResponse({"status": "plugged_out", "adapter_name": name})

    # --- Upstream External Servers APIs ---

    async def register_server_endpoint(request):
        try:
            data = await request.json()
            server_type = data.get("server_type", "mcp").lower()
            client_headers = extract_forwardable_headers(request)

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

    async def list_servers_endpoint(request):
        external_mcp_servers = upstream_proxy_registry.list_servers()
        openapi_servers = openapi_bridge_registry.list_servers()
        return JSONResponse({
            "status": "success",
            "external_upstream_servers": external_mcp_servers,
            "openapi_bridge_servers": openapi_servers,
        })

    async def unregister_server_endpoint(request):
        server_id = request.path_params.get("server_id")
        removed = upstream_proxy_registry.unregister_server(server_id)
        if not removed:
            return JSONResponse({"error": "Not Found", "message": f"External MCP server '{server_id}' not found"}, status_code=404)
        return JSONResponse({"status": "unregistered", "server_id": server_id})

    async def global_mcp_router_endpoint(request):
        return await ToolGatewayRouter.handle_mcp_request(request)

    return [
        Route("/", homepage, methods=["GET"]),
        Route("/mcp", global_mcp_router_endpoint, methods=["POST"]),
        Route("/adapters", list_adapters_endpoint, methods=["GET"]),
        Route("/adapters", create_adapter_endpoint, methods=["POST"]),
        Route("/adapters/{name}", get_adapter_endpoint, methods=["GET"]),
        Route("/adapters/{name}/status", get_adapter_status_endpoint, methods=["GET"]),
        Route("/adapters/{name}/logs", get_adapter_logs_endpoint, methods=["GET"]),
        Route("/adapters/{name}/tools", get_adapter_tools_endpoint, methods=["GET"]),
        Route("/adapters/{name}", update_adapter_endpoint, methods=["PUT"]),
        Route("/adapters/{name}", delete_adapter_endpoint, methods=["DELETE"]),
        Route("/metrics", metrics_endpoint, methods=["GET"]),
        Route("/servers", list_servers_endpoint, methods=["GET"]),
        Route("/servers/register", register_server_endpoint, methods=["POST"]),
        Route("/servers/{server_id}", unregister_server_endpoint, methods=["DELETE"]),
    ]
