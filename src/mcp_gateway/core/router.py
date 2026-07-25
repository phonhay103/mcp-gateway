"""
Global Tool Gateway Router Engine (POST /mcp Data Plane).
Acts as an intelligent router aggregating tools across all plugged-in adapters (Local Mocks, Scenarios, Real Upstream MCP Servers).
Applies system-wide specialized mock features (Chaos Fault Injection, Time-Travel Snapshots, Trace Recording,
Schema Hash-Pinning, Description Sanitization, Per-Call Auth) across all tool call executions transparently.
"""

import time
from typing import Any, Dict, List, Optional
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp_gateway.core.adapters import AdapterStatus, AdapterType, adapter_registry
from mcp_gateway.security.auth import PerCallAuthorizer
from mcp_gateway.logger import logger
from mcp_gateway.observability.metrics import metrics_collector
from mcp_gateway.core.proxy import upstream_proxy_registry
from mcp_gateway.observability.recorder import trace_recorder
from mcp_gateway.core.schemas import schema_registry
from mcp_gateway.security import ToolDescriptionSanitizer, hash_pinning_guard
from mcp_gateway.session import session_manager
from mcp_gateway.core.middleware.header_validation import validate_mcp_headers
from mcp_gateway.core.middleware.chaos_middleware import evaluate_chaos
from mcp_gateway.core.extensions.mcp_extensions import task_manager


class ToolGatewayRouter:
    """Global Router handling central POST /mcp JSON-RPC requests."""

    @staticmethod
    async def handle_mcp_request(request: Request) -> Response:
        """Process incoming Streamable HTTP JSON-RPC request at POST /mcp."""
        session_id = getattr(request.state, "session_id", "default_session")
        scenario_id = getattr(request.state, "scenario_id", "default_scenario")

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

        # 2. Handle Tasks Extension Methods (MCP 2026-07-28)
        if method in ("tasks/get", "tasks/list", "tasks/cancel", "tasks/update"):
            task_id = params.get("taskId") or params.get("id") or "task-mock-2026-001"
            metrics_collector.record_request("POST", "/mcp", 200)

            if method == "tasks/get":
                task = task_manager.get_task(task_id)
                if not task:
                    task = task_manager.create_task(task_id, "default_tool", {})
                res_data = task
            elif method == "tasks/update":
                updates = params.get("inputResponses", {"status": "completed"})
                task = task_manager.update_task(task_id, {"status": "completed", "progress": 100, "result": updates})
                if not task:
                    task = task_manager.create_task(task_id, "default_tool", updates)
                res_data = task
            elif method == "tasks/cancel":
                success = task_manager.cancel_task(task_id)
                res_data = {"taskId": task_id, "status": "cancelled" if success else "notFound"}
            else: # tasks/list
                res_data = {"tasks": list(task_manager._tasks.values())}

            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": res_data
            })

        # 3. Handle server/discover method (MCP 2026-07-28 stateless capability discovery)
        if method == "server/discover":
            metrics_collector.record_request("POST", "/mcp", 200)
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2026-07-28",
                    "serverInfo": {"name": "Global Tool Gateway Router", "version": "2026.07.28"},
                    "capabilities": {
                        "statelessCore": True,
                        "extensions": {
                            "mcp-apps": {"supported": True, "version": "1.0"},
                            "tasks": {"supported": True, "version": "1.0"}
                        }
                    },
                    "endpoints": {"mcp": "/mcp", "adapters": "/adapters"}
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

        # 6. Handle tools/list method (Aggregates across all running adapters, handling duplicate names with namespace qualification)
        if method == "tools/list":
            filter_servers = params.get("servers") or params.get("adapters") or params.get("mcp")
            if not filter_servers:
                q_mcp = request.query_params.get("mcp") or request.query_params.get("servers")
                if q_mcp:
                    filter_servers = [s.strip() for s in q_mcp.split(",") if s.strip()]

            aggregated_tools = []
            seen_tools = set()
            running_adapters = adapter_registry.list_adapters()

            for adapter in running_adapters:
                if adapter.status != AdapterStatus.RUNNING:
                    continue
                if filter_servers and adapter.name not in filter_servers:
                    continue

                for tool_name in adapter.tools:
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

                    # 2. Always expose fully-qualified namespaced version (adapter::tool) to resolve collisions explicitly
                    qualified_name = f"{adapter.name}::{tool_name}"
                    qual_formatted = tool_def.to_mcp_format()
                    qual_formatted["name"] = qualified_name
                    qual_formatted["description"] = f"[{adapter.name}] {ToolDescriptionSanitizer.sanitize_description(tool_def.description)}"
                    aggregated_tools.append(qual_formatted)

            metrics_collector.record_request("POST", "/mcp", 200)
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
            target_server = params.get("server") or params.get("adapter") or params.get("mcp")
            actual_tool_name = raw_tool_name
            if "::" in raw_tool_name:
                parts = raw_tool_name.split("::", 1)
                target_server = parts[0]
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

            # Measure Execution & Initial State for Trace
            start_time = time.time()
            session = session_manager.get_session(session_id=session_id)
            initial_state = dict(session.state)

            # SYSTEM-WIDE SPECIALIZED MOCK FEATURE: Chaos & Fault Injection Middleware Evaluation
            chaos_response = evaluate_chaos(scenario_id, tool_name, tool_args, req_id)
            if chaos_response:
                duration_ms = (time.time() - start_time) * 1000
                trace_recorder.record_call(
                    session_id=session.session_id,
                    scenario_id=scenario_id,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    initial_state=initial_state,
                    final_state=dict(session.state),
                    duration_ms=duration_ms,
                    is_error=True,
                    error_details={"message": "Chaos Fault Injected"},
                )
                return chaos_response

            # Identify target adapter handling this tool with duplicate collision resolution & scenario priority
            target_adapter = None
            candidate_adapters = []

            # 1. Prioritize adapter matching current scenario_id if valid
            if scenario_id and scenario_id != "default_scenario":
                sc_adapter = adapter_registry.get_adapter(scenario_id)
                if sc_adapter and sc_adapter.status == AdapterStatus.RUNNING and tool_name in sc_adapter.tools:
                    target_adapter = sc_adapter

            if not target_adapter:
                if target_server:
                    adapter = adapter_registry.get_adapter(target_server)
                    if adapter and adapter.status == AdapterStatus.RUNNING and tool_name in adapter.tools:
                        target_adapter = adapter
                    if not target_adapter:
                        return JSONResponse({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32602,
                                "message": f"Target MCP server/adapter '{target_server}' not found or does not provide tool '{tool_name}'",
                            },
                        }, status_code=400)
                else:
                    for adapter in adapter_registry.list_adapters():
                        if tool_name in adapter.tools and adapter.status == AdapterStatus.RUNNING:
                            candidate_adapters.append(adapter)

                    if len(candidate_adapters) == 1:
                        target_adapter = candidate_adapters[0]
                    elif len(candidate_adapters) > 1:
                        candidate_names = [a.name for a in candidate_adapters]
                        return JSONResponse({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32603,
                                "message": (
                                    f"Ambiguous tool name collision: Tool '{tool_name}' is provided by multiple MCP adapters: {candidate_names}. "
                                    f"Please specify 'server': '<adapter_name>' in parameters, use fully qualified name '<adapter_name>::{tool_name}', or set scenario context."
                                ),
                            },
                        }, status_code=400)
                    else:
                        target_adapter = None

            execution_result = None
            is_error = False

            if target_adapter and target_adapter.adapter_type == AdapterType.REAL_UPSTREAM:
                # Proxy tool call to real upstream MCP server
                upstream_res = await upstream_proxy_registry.forward_tool_call(
                    server_id=target_adapter.name,
                    tool_name=tool_name,
                    tool_args=tool_args,
                )
                duration_ms = (time.time() - start_time) * 1000
                final_state = dict(session.state)

                trace_recorder.record_call(
                    session_id=session.session_id,
                    scenario_id=scenario_id,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    initial_state=initial_state,
                    final_state=final_state,
                    duration_ms=duration_ms,
                    is_error="error" in upstream_res,
                    result_payload=upstream_res.get("result"),
                )
                metrics_collector.record_tool_call(tool_name, "success" if "result" in upstream_res else "error")
                return JSONResponse(upstream_res)

            # Local / Scenario execution fallback via registry tools
            from mcp_gateway.core.registry import AVAILABLE_TOOLS
            if tool_name in AVAILABLE_TOOLS:
                try:
                    func = AVAILABLE_TOOLS[tool_name]
                    res = func(**tool_args) if tool_args else func()

                    if getattr(res, "is_error", False) or (isinstance(res, dict) and res.get("is_error")):
                        is_error = True
                        content_str = str(getattr(res, "content", res))
                        execution_result = {"content": [{"type": "text", "text": content_str}], "isError": True}
                    else:
                        content_data = res if isinstance(res, dict) else {"result": res}
                        execution_result = {
                            "content": [{"type": "text", "text": str(content_data)}],
                            "structuredContent": content_data,
                            "isError": False,
                        }
                except Exception as exc:
                    is_error = True
                    execution_result = {
                        "content": [{"type": "text", "text": f"Tool Execution Error: {exc}"}],
                        "isError": True,
                    }
            else:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Tool '{tool_name}' not found on any running adapter"},
                }, status_code=404)

            duration_ms = (time.time() - start_time) * 1000
            final_state = dict(session.state)

            # SYSTEM-WIDE SPECIALIZED MOCK FEATURE: Execution Trace Recording
            trace_recorder.record_call(
                session_id=session.session_id,
                scenario_id=scenario_id,
                tool_name=tool_name,
                tool_args=tool_args,
                initial_state=initial_state,
                final_state=final_state,
                duration_ms=duration_ms,
                is_error=is_error,
                result_payload=execution_result,
            )
            metrics_collector.record_tool_call(tool_name, "error" if is_error else "success")

            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": execution_result,
            })

        # Default method not supported
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not implemented by Global Router"},
        }, status_code=405)
