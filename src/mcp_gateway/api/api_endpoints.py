"""
REST API Endpoint Handlers for Gateway Control Plane & Management APIs.
Includes /adapters, /scenarios, /sessions, /tools, /servers, /metrics, /repro, and /mcp routes.
"""

from typing import Any, Dict, List
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mcp_gateway.adapters import AdapterStatus, PlugInAdapterConfig, adapter_registry
from mcp_gateway.chaos import FaultRule, FaultType, chaos_engine
from mcp_gateway.logger import logger
from mcp_gateway.metrics import metrics_collector
from mcp_gateway.proxy import UpstreamMCPServerConfig, upstream_proxy_registry
from mcp_gateway.recorder import ReplayEngine, ReproBundle, trace_recorder
from mcp_gateway.router import ToolGatewayRouter
from mcp_gateway.schemas import schema_registry
from mcp_gateway.session import generate_uuid7, session_manager


def create_gateway_routes(server_manager: Any) -> List[Route]:
    """Factory creating Starlette Route definitions bound to ServerManager instance."""

    gateway_scenarios = [sc for sc in server_manager.scenario_configs if sc.enable_gateway]

    async def homepage(request):
        session_id = getattr(request.state, "session_id", "N/A")
        metrics_collector.record_request("GET", "/", 200)
        status_data = {
            "status": "online",
            "mode": "enterprise-scenario-gateway",
            "session_id": session_id,
            "shared_port": server_manager.gateway_port,
            "security": {
                "auth_enabled": server_manager.auth_config.enabled,
                "auth_type": server_manager.auth_config.auth_type,
                "rate_limiting_enabled": server_manager.rate_limit_enabled,
                "rate_limit_rpm": server_manager.rate_limit_rpm,
                "session_tracking_header": server_manager.yaml_config.gateway.header_session_name,
                "scenario_tracking_header": server_manager.yaml_config.gateway.header_scenario_name,
                "query_param_auth": "disabled",
            },
            "scenario_count": len(gateway_scenarios),
            "endpoints": {
                "metrics": "/metrics",
                "global_router": "/mcp",
                "list_adapters": "/adapters",
                "create_adapter": "/adapters",
                "get_adapter": "/adapters/{name}",
                "get_adapter_status": "/adapters/{name}/status",
                "get_adapter_logs": "/adapters/{name}/logs",
                "list_scenarios": "/scenarios",
                "select_scenario": "/scenarios/select",
                "clear_scenario": "/scenarios/clear",
                "multiplexed_tools": "/scenarios/multiplexed/tools",
                "list_sessions": "/sessions",
                "create_session": "/sessions",
                "close_session": "/sessions/{session_id}",
                "list_tools": "/tools",
                "get_tool": "/tools/{tool_name}",
                "scenario_tools": "/scenarios/{scenario_id}/tools",
                "session_trace": "/sessions/{session_id}/trace",
                "export_repro": "/sessions/{session_id}/repro",
                "replay_repro": "/repro/replay",
                "scenarios_chaos": "/scenarios/{scenario_id}/chaos",
                "create_snapshot": "/sessions/{session_id}/snapshots",
                "restore_snapshot": "/sessions/{session_id}/snapshots/restore",
                "register_server": "/servers/register",
                "list_servers": "/servers",
                "unregister_server": "/servers/{server_id}",
            },
        }
        return JSONResponse(status_data)

    async def metrics_endpoint(request):
        """GET /metrics: Prometheus & OpenTelemetry compatible metrics endpoint."""
        active_count = len(session_manager.list_active_sessions())
        content = metrics_collector.generate_prometheus_format(active_sessions_count=active_count)
        return Response(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")

    # --- Adapters Control Plane APIs ---

    async def list_adapters_endpoint(request):
        """GET /adapters: List all plugged-in MCP adapters in Control Plane."""
        adapters = adapter_registry.list_adapters()
        return JSONResponse({
            "status": "success",
            "count": len(adapters),
            "adapters": [a.model_dump() for a in adapters],
        })

    async def create_adapter_endpoint(request):
        """POST /adapters: Plug-in and deploy a new MCP Server Adapter."""
        try:
            data = await request.json()
            config = PlugInAdapterConfig.model_validate(data)
            registered = adapter_registry.register_adapter(config)
            return JSONResponse({"status": "plugged_in", "adapter": registered.model_dump()}, status_code=201)
        except Exception as exc:
            return JSONResponse({"error": "Bad Request", "message": f"Failed to plug-in adapter: {exc}"}, status_code=400)

    async def get_adapter_endpoint(request):
        """GET /adapters/{name}: Retrieve configuration for a specific plugged-in adapter."""
        name = request.path_params.get("name")
        adapter = adapter_registry.get_adapter(name)
        if not adapter:
            return JSONResponse({"error": "Not Found", "message": f"Adapter '{name}' not found"}, status_code=404)
        return JSONResponse(adapter.model_dump())

    async def get_adapter_status_endpoint(request):
        """GET /adapters/{name}/status: Retrieve operational status for an adapter."""
        name = request.path_params.get("name")
        status_data = adapter_registry.get_adapter_status(name)
        if status_data.get("status") == "NotFound":
            return JSONResponse({"error": "Not Found", "message": status_data.get("error")}, status_code=404)
        return JSONResponse(status_data)

    async def get_adapter_logs_endpoint(request):
        """GET /adapters/{name}/logs: Retrieve runtime logs for an adapter."""
        name = request.path_params.get("name")
        logs = adapter_registry.get_adapter_logs(name)
        return JSONResponse({"status": "success", "adapter_name": name, "count": len(logs), "logs": logs})

    async def get_adapter_tools_endpoint(request):
        """GET /adapters/{name}/tools: List all tools provided by a specific MCP adapter/server."""
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
        """PUT /adapters/{name}: Update configuration or status of a plugged-in adapter."""
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
        """DELETE /adapters/{name}: Plug-out and unregister an MCP Server Adapter."""
        name = request.path_params.get("name")
        unregistered = adapter_registry.unregister_adapter(name)
        if not unregistered:
            return JSONResponse({"error": "Not Found", "message": f"Adapter '{name}' not found"}, status_code=404)
        return JSONResponse({"status": "plugged_out", "adapter_name": name})

    # --- Scenarios APIs ---

    async def list_scenarios_endpoint(request):
        """GET /scenarios: List all available scenarios."""
        session_id = getattr(request.state, "session_id", "N/A")
        metrics_collector.record_request("GET", "/scenarios", 200)
        sc_list = []
        for sc in gateway_scenarios:
            tools, resources, prompts = server_manager._resolve_scenario_capabilities(sc)
            endpoint = f"{sc.prefix}/mcp"
            sc_list.append({
                "id": sc.id,
                "name": sc.name,
                "description": sc.description,
                "transport": sc.transport,
                "prefix": sc.prefix,
                "endpoint": endpoint,
                "gateway_port": server_manager.gateway_port,
                "referenced_servers": sc.servers,
                "fixtures": sc.fixtures,
                "tools": tools,
                "resources": resources,
                "prompts": prompts,
            })
        return JSONResponse({
            "status": "success",
            "count": len(sc_list),
            "session_id": session_id,
            "scenarios": sc_list,
        })

    async def select_scenario_endpoint(request):
        """POST /scenarios/select: Bind a session to a specific scenario."""
        session_id = getattr(request.state, "session_id", "N/A")
        try:
            data = await request.json()
        except Exception:
            data = {}

        target_scenario_id = data.get("scenario_id") or request.headers.get("X-MCP-Scenario")
        target_session_id = data.get("session_id") or session_id

        if not target_scenario_id:
            return JSONResponse(
                {"error": "Bad Request", "message": "Missing 'scenario_id' in JSON body or 'X-MCP-Scenario' header."},
                status_code=400,
            )

        scenario_obj = next((sc for sc in server_manager.scenario_configs if sc.id == target_scenario_id), None)
        if not scenario_obj:
            return JSONResponse(
                {"error": "Not Found", "message": f"Scenario '{target_scenario_id}' not found."},
                status_code=404,
            )

        fixture_state = server_manager.scenario_fixture_states.get(target_scenario_id, {})
        session_manager.get_or_create_session(
            session_id=target_session_id,
            scenario_id=target_scenario_id,
            server_id=target_scenario_id,
            initial_state=fixture_state,
        )

        tools, resources, prompts = server_manager._resolve_scenario_capabilities(scenario_obj)
        return JSONResponse({
            "status": "selected",
            "session_id": target_session_id,
            "active_scenario": {
                "id": scenario_obj.id,
                "name": scenario_obj.name,
                "prefix": scenario_obj.prefix,
                "endpoint": f"{scenario_obj.prefix}/mcp",
                "tools": tools,
                "resources": resources,
                "prompts": prompts,
            },
        })

    async def clear_scenario_endpoint(request):
        """POST /scenarios/clear: Reset session state for a given session / scenario."""
        session_id = getattr(request.state, "session_id", "N/A")
        try:
            data = await request.json()
        except Exception:
            data = {}

        target_session_id = data.get("session_id") or session_id
        target_scenario_id = data.get("scenario_id")

        cleared_count = session_manager.clear_session(target_session_id, target_scenario_id)

        return JSONResponse({
            "status": "cleared",
            "session_id": target_session_id,
            "cleared_scenario_id": target_scenario_id or "all",
            "cleared_records": cleared_count,
        })

    async def multiplexed_tools_endpoint(request):
        """GET /scenarios/multiplexed/tools: Multiplexed tool list with server namespace prefixing."""
        fmt = request.query_params.get("format", "mcp")
        multiplexed_list = []

        for sc in gateway_scenarios:
            tools, _, _ = server_manager._resolve_scenario_capabilities(sc)
            for tool_name in tools:
                tool_def = schema_registry.get_tool(tool_name)
                if tool_def:
                    prefixed_name = f"{sc.id}__{tool_def.name}"
                    item = tool_def.to_mcp_format()
                    item["name"] = prefixed_name
                    item["original_name"] = tool_def.name
                    item["scenario_id"] = sc.id
                    multiplexed_list.append(item)

        return JSONResponse({
            "status": "success",
            "mode": "server-multiplexing",
            "count": len(multiplexed_list),
            "tools": multiplexed_list,
        })

    # --- Sessions APIs ---

    async def list_sessions_endpoint(request):
        """GET /sessions: List all active sessions and their bound scenarios."""
        active_sessions = session_manager.list_active_sessions()
        return JSONResponse({
            "status": "success",
            "count": len(active_sessions),
            "sessions": active_sessions,
        })

    async def create_session_endpoint(request):
        """POST /sessions: Explicitly initialize/create a new isolated session (auto-generates UUID v7)."""
        try:
            data = await request.json()
        except Exception:
            data = {}

        new_session_id = data.get("session_id") or generate_uuid7()
        scenario_id = data.get("scenario_id") or "default_scenario"
        metadata = data.get("metadata", {})

        fixture_state = server_manager.scenario_fixture_states.get(scenario_id, {})
        session_data = session_manager.get_or_create_session(
            session_id=new_session_id,
            scenario_id=scenario_id,
            server_id=scenario_id,
            initial_state=fixture_state,
            metadata=metadata,
        )

        return JSONResponse(
            {
                "status": "created",
                "session_id": session_data.session_id,
                "scenario_id": session_data.scenario_id,
                "created_at": session_data.created_at,
                "metadata": session_data.metadata,
            },
            status_code=201,
        )

    async def close_session_endpoint(request):
        """DELETE /sessions/{session_id} or POST /sessions/close: Close & evict session state."""
        session_id = request.path_params.get("session_id")
        if not session_id:
            try:
                data = await request.json()
                session_id = data.get("session_id")
            except Exception:
                session_id = None

        if not session_id:
            return JSONResponse(
                {"error": "Bad Request", "message": "Missing 'session_id' in path or JSON body."},
                status_code=400,
            )

        evicted_records = session_manager.close_session(session_id)
        return JSONResponse({
            "status": "closed",
            "session_id": session_id,
            "evicted_records": evicted_records,
        })

    # --- Tools Catalog APIs ---

    async def list_tools_endpoint(request):
        """GET /tools: List tool JSON schemas."""
        fmt = request.query_params.get("format", "mcp")
        scenario_id = request.query_params.get("scenario_id")

        filter_tools = None
        if scenario_id:
            sc_obj = next((sc for sc in server_manager.scenario_configs if sc.id == scenario_id), None)
            if sc_obj:
                filter_tools, _, _ = server_manager._resolve_scenario_capabilities(sc_obj)
            else:
                return JSONResponse(
                    {"error": "Not Found", "message": f"Scenario '{scenario_id}' not found."},
                    status_code=404,
                )

        tools_list = schema_registry.list_tools(filter_names=filter_tools, format_type=fmt)
        return JSONResponse({
            "status": "success",
            "format": fmt,
            "count": len(tools_list),
            "scenario_id": scenario_id or "all",
            "tools": tools_list,
        })

    async def get_tool_endpoint(request):
        """GET /tools/{tool_name}: Retrieve JSON schema for a specific tool by name."""
        tool_name = request.path_params.get("tool_name")
        fmt = request.query_params.get("format", "mcp")
        tool_def = schema_registry.get_tool(tool_name)
        if not tool_def:
            return JSONResponse(
                {"error": "Not Found", "message": f"Tool '{tool_name}' not found in schema registry."},
                status_code=404,
            )

        if fmt == "openai":
            return JSONResponse(tool_def.to_openai_format())
        elif fmt == "raw":
            return JSONResponse(tool_def.model_dump())

        return JSONResponse(tool_def.to_mcp_format())

    async def scenario_tools_endpoint(request):
        """GET /scenarios/{scenario_id}/tools: Retrieve tool JSON schemas for a specific scenario whitelist."""
        scenario_id = request.path_params.get("scenario_id")
        fmt = request.query_params.get("format", "mcp")

        sc_obj = next((sc for sc in server_manager.scenario_configs if sc.id == scenario_id), None)
        if not sc_obj:
            return JSONResponse(
                {"error": "Not Found", "message": f"Scenario '{scenario_id}' not found."},
                status_code=404,
            )

        filter_tools, _, _ = server_manager._resolve_scenario_capabilities(sc_obj)
        tools_list = schema_registry.list_tools(filter_names=filter_tools, format_type=fmt)
        return JSONResponse({
            "status": "success",
            "scenario_id": scenario_id,
            "format": fmt,
            "count": len(tools_list),
            "tools": tools_list,
        })

    # --- Debugging & Chaos APIs ---

    async def session_trace_endpoint(request):
        """GET /sessions/{session_id}/trace: Retrieve chronological tool execution trace log."""
        session_id = request.path_params.get("session_id")
        records = trace_recorder.get_session_trace(session_id)
        return JSONResponse({
            "status": "success",
            "session_id": session_id,
            "count": len(records),
            "trace": [r.model_dump() for r in records],
        })

    async def export_repro_endpoint(request):
        """GET /sessions/{session_id}/repro: Export self-contained .mcp-repro.json bundle."""
        session_id = request.path_params.get("session_id")
        session = session_manager.get_session(session_id=session_id)
        faults = chaos_engine.list_rules(scenario_id=session.scenario_id)

        bundle = trace_recorder.export_repro_bundle(
            session_id=session.session_id,
            scenario_id=session.scenario_id,
            initial_state=session.state,
            fault_rules=faults,
        )
        return JSONResponse(bundle.model_dump())

    async def replay_repro_endpoint(request):
        """POST /repro/replay: Import and deterministically replay a .mcp-repro.json bundle."""
        try:
            body = await request.json()
            bundle = ReproBundle.model_validate(body)
        except Exception as exc:
            return JSONResponse(
                {"error": "Bad Request", "message": f"Invalid ReproBundle JSON: {exc}"},
                status_code=400,
            )

        async def _dummy_executor(sc_id, tool_name, args):
            return {"status": "replayed", "tool": tool_name}

        report = await ReplayEngine.replay_bundle(bundle, _dummy_executor)
        return JSONResponse(report.model_dump())

    async def scenarios_chaos_endpoint(request):
        """POST /scenarios/{scenario_id}/chaos: Add dynamic chaos fault injection rules."""
        scenario_id = request.path_params.get("scenario_id")
        try:
            data = await request.json()
        except Exception:
            data = {}

        ftype_str = data.get("fault_type", "latency")
        try:
            ftype = FaultType(ftype_str)
        except ValueError:
            ftype = FaultType.LATENCY

        rule = FaultRule(
            scenario_id=scenario_id,
            tool_name=data.get("tool_name", "*"),
            fault_type=ftype,
            rate=float(data.get("rate", 1.0)),
            delay_ms=float(data.get("delay_ms", 0.0)),
            error_message=data.get("error_message", "Simulated chaos fault"),
            http_status=data.get("http_status"),
        )
        rule_id = chaos_engine.add_rule(rule)
        return JSONResponse({"status": "active", "rule_id": rule_id, "rule": rule.model_dump()})

    async def clear_chaos_endpoint(request):
        """DELETE /scenarios/{scenario_id}/chaos: Clear fault rules for a scenario."""
        scenario_id = request.path_params.get("scenario_id")
        cleared = chaos_engine.clear_rules(scenario_id=scenario_id)
        return JSONResponse({"status": "cleared", "scenario_id": scenario_id, "cleared_count": cleared})

    async def create_snapshot_endpoint(request):
        """POST /sessions/{session_id}/snapshots: Create time-travel state snapshot."""
        session_id = request.path_params.get("session_id")
        try:
            data = await request.json()
        except Exception:
            data = {}

        label = data.get("label", "")
        session = session_manager.get_session(session_id=session_id)
        snap_id = session.create_snapshot(label=label)
        return JSONResponse({"status": "created", "session_id": session_id, "snapshot_id": snap_id}, status_code=201)

    async def restore_snapshot_endpoint(request):
        """POST /sessions/{session_id}/snapshots/restore: Rollback session state to snapshot."""
        session_id = request.path_params.get("session_id")
        try:
            data = await request.json()
        except Exception:
            data = {}

        snap_id = data.get("snapshot_id")
        if not snap_id:
            return JSONResponse({"error": "Bad Request", "message": "Missing 'snapshot_id'"}, status_code=400)

        session = session_manager.get_session(session_id=session_id)
        ok = session.restore_snapshot(snap_id)
        if not ok:
            return JSONResponse({"error": "Not Found", "message": f"Snapshot '{snap_id}' not found"}, status_code=404)

        return JSONResponse({"status": "restored", "session_id": session_id, "snapshot_id": snap_id})

    # --- Upstream External Servers APIs ---

    async def register_server_endpoint(request):
        """POST /servers/register: Dynamically register a real external HTTP/Streamable-HTTP MCP server."""
        try:
            data = await request.json()
            config = UpstreamMCPServerConfig.model_validate(data)
            upstream_proxy_registry.register_server(config)

            discovered_count = 0
            try:
                tools = await upstream_proxy_registry.discover_upstream_tools(config.server_id)
                discovered_count = len(tools)
            except Exception as exc:
                logger.warning(f"Registered external server [{config.server_id}] but tools auto-discovery pending: {exc}")

            return JSONResponse({
                "status": "registered",
                "server_id": config.server_id,
                "upstream_url": config.upstream_url,
                "discovered_tools_count": discovered_count,
            }, status_code=201)
        except Exception as exc:
            return JSONResponse({"error": "Bad Request", "message": f"Failed to register server: {exc}"}, status_code=400)

    async def list_servers_endpoint(request):
        """GET /servers: List all registered local and external real MCP servers."""
        external_servers = upstream_proxy_registry.list_servers()
        local_servers = [s.model_dump() for s in server_manager.yaml_config.servers] if server_manager.yaml_config else []
        return JSONResponse({
            "status": "success",
            "local_servers": local_servers,
            "external_upstream_servers": external_servers,
        })

    async def unregister_server_endpoint(request):
        """DELETE /servers/{server_id}: Unregister a real external MCP server."""
        server_id = request.path_params.get("server_id")
        removed = upstream_proxy_registry.unregister_server(server_id)
        if not removed:
            return JSONResponse({"error": "Not Found", "message": f"External MCP server '{server_id}' not found"}, status_code=404)
        return JSONResponse({"status": "unregistered", "server_id": server_id})

    async def global_mcp_router_endpoint(request):
        """POST /mcp: Global Tool Gateway Router endpoint directing JSON-RPC requests across all plugged-in adapters."""
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
        Route("/scenarios", list_scenarios_endpoint, methods=["GET"]),
        Route("/scenarios/select", select_scenario_endpoint, methods=["POST"]),
        Route("/scenarios/clear", clear_scenario_endpoint, methods=["POST"]),
        Route("/scenarios/multiplexed/tools", multiplexed_tools_endpoint, methods=["GET"]),
        Route("/scenarios/{scenario_id}/tools", scenario_tools_endpoint, methods=["GET"]),
        Route("/scenarios/{scenario_id}/chaos", scenarios_chaos_endpoint, methods=["POST"]),
        Route("/scenarios/{scenario_id}/chaos", clear_chaos_endpoint, methods=["DELETE"]),
        Route("/sessions", list_sessions_endpoint, methods=["GET"]),
        Route("/sessions", create_session_endpoint, methods=["POST"]),
        Route("/sessions/close", close_session_endpoint, methods=["POST"]),
        Route("/sessions/{session_id}", close_session_endpoint, methods=["DELETE"]),
        Route("/sessions/{session_id}/trace", session_trace_endpoint, methods=["GET"]),
        Route("/sessions/{session_id}/repro", export_repro_endpoint, methods=["GET"]),
        Route("/sessions/{session_id}/snapshots", create_snapshot_endpoint, methods=["POST"]),
        Route("/sessions/{session_id}/snapshots/restore", restore_snapshot_endpoint, methods=["POST"]),
        Route("/repro/replay", replay_repro_endpoint, methods=["POST"]),
        Route("/servers", list_servers_endpoint, methods=["GET"]),
        Route("/servers/register", register_server_endpoint, methods=["POST"]),
        Route("/servers/{server_id}", unregister_server_endpoint, methods=["DELETE"]),
        Route("/tools", list_tools_endpoint, methods=["GET"]),
        Route("/tools/{tool_name}", get_tool_endpoint, methods=["GET"]),
    ]
