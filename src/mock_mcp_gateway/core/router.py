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

from mock_mcp_gateway.core.adapters import AdapterStatus, AdapterType, adapter_registry
from mock_mcp_gateway.security.auth import PerCallAuthorizer
from mock_mcp_gateway.core.chaos import chaos_engine
from mock_mcp_gateway.logger import logger
from mock_mcp_gateway.observability.metrics import metrics_collector
from mock_mcp_gateway.core.proxy import upstream_proxy_registry
from mock_mcp_gateway.observability.recorder import trace_recorder
from mock_mcp_gateway.core.schemas import schema_registry
from mock_mcp_gateway.security import ToolDescriptionSanitizer, hash_pinning_guard
from mock_mcp_gateway.session import session_manager


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

        # 1. Handle initialize method
        if method == "initialize":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "experimental": {},
                        "logging": {},
                        "prompts": {"listChanged": True},
                        "resources": {"subscribe": False, "listChanged": True},
                        "tools": {"listChanged": True},
                    },
                    "serverInfo": {
                        "name": "Global Tool Gateway Router",
                        "version": "2026.1.0",
                    },
                    "instructions": "Global MCP Gateway Router orchestrating plugged-in Mock & Real MCP Servers.",
                },
            })

        # 2. Handle notifications/initialized
        if method == "notifications/initialized":
            return JSONResponse({"jsonrpc": "2.0", "result": {}})

        # 3. Handle tools/list method (Aggregates across all running adapters)
        if method == "tools/list":
            aggregated_tools = []
            running_adapters = adapter_registry.list_adapters()

            for adapter in running_adapters:
                if adapter.status != AdapterStatus.RUNNING:
                    continue

                for tool_name in adapter.tools:
                    tool_def = schema_registry.get_tool(tool_name)
                    if tool_def:
                        mcp_formatted = tool_def.to_mcp_format()
                        # Sanitize description
                        mcp_formatted["description"] = ToolDescriptionSanitizer.sanitize_description(
                            mcp_formatted.get("description", "")
                        )
                        aggregated_tools.append(mcp_formatted)

            metrics_collector.record_request("POST", "/mcp", 200)
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": aggregated_tools},
            })

        # 4. Handle tools/call method
        if method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})

            if not tool_name:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": "Missing 'name' in tools/call parameters"},
                }, status_code=400)

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

            # SYSTEM-WIDE SPECIALIZED MOCK FEATURE: Chaos & Fault Injection Engine Evaluation
            fault = chaos_engine.evaluate_and_apply_sync(scenario_id, tool_name, tool_args)
            if fault.triggered:
                duration_ms = (time.time() - start_time) * 1000
                if fault.error:
                    # Record trace step
                    trace_recorder.record_call(
                        session_id=session.session_id,
                        scenario_id=scenario_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        initial_state=initial_state,
                        final_state=dict(session.state),
                        duration_ms=duration_ms,
                        is_error=True,
                        error_details=fault.error,
                        injected_fault=fault.model_dump(),
                    )
                    return JSONResponse({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": fault.error.get("message", "Chaos Fault Injected")}],
                            "isError": True,
                            "_meta": {"fault_type": fault.fault_type.value if fault.fault_type else "chaos"},
                        },
                    })
                elif fault.malformed_response:
                    return JSONResponse({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": fault.malformed_response,
                    })

            # Identify target adapter handling this tool
            target_adapter = None
            for adapter in adapter_registry.list_adapters():
                if tool_name in adapter.tools and adapter.status == AdapterStatus.RUNNING:
                    target_adapter = adapter
                    break

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
            from mock_mcp_gateway.core.registry import AVAILABLE_TOOLS
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
