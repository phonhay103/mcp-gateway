"""
Tool, resource, and prompt implementations for Mock MCP using Pydantic v2 schemas and 3-Way Session Isolation.
MCP Tools remain scenario-agnostic pure functions operating on isolated session state.
Tool parameter schemas are annotated via typing.Annotated & Pydantic Field for FastMCP native tools/list protocol serving.
Supports detailed tool-level result overrides or MCP standard execution errors with isError: true.
Includes Dynamic Tool Discovery (find_tools, describe_tools) & Code Mode Sandboxed Execution (execute_tool_code).
"""

from datetime import datetime, timezone
import ast
import json
import operator
import os
import platform
import re
import sys
import time
import urllib.request
import uuid
from typing import Annotated, Any, Dict, List, Optional
from fastmcp.tools.base import ToolResult
from pydantic import BaseModel, Field

from mock_mcp_gateway.session import session_manager

# Track server start time
_SERVER_START_TIME = time.time()


# --- Pydantic Data Models ---

class UserRecord(BaseModel):
    """Pydantic model representing a User entity."""
    id: str = Field(..., description="Unique User ID")
    name: str = Field(..., description="Full Name")
    email: str = Field(..., description="Email address")
    role: str = Field(default="user", description="User access role")


class SystemInfoModel(BaseModel):
    """Pydantic model for System Info output."""
    os: str
    os_release: str
    architecture: str
    python_version: str
    cpu_count: Optional[int]
    process_id: int
    uptime_seconds: float
    session_id: str
    scenario_id: str
    server_id: str
    metrics: Optional[Dict[str, Any]] = None


class EchoOutput(BaseModel):
    """Pydantic model for Echo tool output."""
    original_message: str
    length: int
    reversed: str
    uppercase: str
    timestamp_utc: str
    session_id: str
    scenario_id: str
    server_id: str


# --- Helper for Detailed Tool-Level Scenario Behavior (Error or Mock Result) ---

def check_scenario_tool_behavior(
    tool_name: str,
    session_id: Optional[str] = None,
    scenario_id: Optional[str] = None,
    server_id: Optional[str] = None,
) -> Optional[ToolResult | Dict[str, Any]]:
    """
    Check if the active scenario or chaos engine defines a tool-level result override, MCP error definition (isError: true), or injected fault.
    Returns ToolResult (if error defined) or dict (if mock_result defined), or None if normal execution.
    """
    from mock_mcp_gateway.chaos import chaos_engine

    session = session_manager.get_session(session_id, scenario_id, server_id)

    # 1. Check Chaos Engine fault injection rules
    fault = chaos_engine.evaluate_and_apply_sync(session.scenario_id, tool_name)
    if fault.triggered:
        if fault.error:
            return ToolResult(
                content=fault.error.get("message", "Simulated chaos fault"),
                meta={"code": fault.error.get("code"), "fault_type": fault.error.get("fault_type")},
                is_error=True,
            )
        elif fault.malformed_response:
            return fault.malformed_response

    # 2. Check Scenario-level tool behaviors
    behavior = session.get_tool_behavior(tool_name)
    if not behavior:
        # Fallback to legacy error simulations if present
        rule = session.get_error_simulation(tool_name)
        if rule:
            message = rule.get(
                "message",
                f"MCP Error simulated for tool '{tool_name}' in scenario '{session.scenario_id}'",
            )
            is_err = rule.get("is_error", True)
            return ToolResult(content=message, is_error=is_err)
        return None

    # 1. Check tool-level error definition
    err_cfg = behavior.get("error")
    if err_cfg and isinstance(err_cfg, dict):
        is_err = err_cfg.get("is_error", True)
        if is_err:
            msg = err_cfg.get("message", f"MCP Tool Error defined for '{tool_name}' in scenario '{session.scenario_id}'")
            code = err_cfg.get("code")
            details = err_cfg.get("details")

            meta = {}
            if code is not None:
                meta["code"] = code
            if details:
                meta["details"] = details

            formatted_msg = f"[Error Code {code}] {msg}" if code is not None else msg
            return ToolResult(
                content=formatted_msg,
                meta=meta if meta else None,
                is_error=True,
            )

    # 2. Check tool-level mock result override
    mock_res = behavior.get("mock_result")
    if mock_res and isinstance(mock_res, dict):
        res = dict(mock_res)
        res["_is_mock"] = True
        res["session_id"] = session.session_id
        res["scenario_id"] = session.scenario_id
        res["server_id"] = session.server_id
        return res

    return None


# --- Tool Functions (Scenario-Agnostic with Annotated FastMCP Schemas) ---

def echo(
    message: Annotated[str, Field(description="Message string to echo back")]
) -> Dict[str, Any] | ToolResult:
    """Echo back the provided message along with helpful string metadata and active session context."""
    tb = check_scenario_tool_behavior("echo")
    if tb is not None:
        return tb

    session = session_manager.get_session()
    out = EchoOutput(
        original_message=message,
        length=len(message),
        reversed=message[::-1],
        uppercase=message.upper(),
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        session_id=session.session_id,
        scenario_id=session.scenario_id,
        server_id=session.server_id,
    )
    return out.model_dump()


def get_system_info() -> Dict[str, Any] | ToolResult:
    """Retrieve system runtime information combined with session fixture state."""
    tb = check_scenario_tool_behavior("get_system_info")
    if tb is not None:
        return tb

    session = session_manager.get_session()
    metrics = session.state.get("system_info")

    info = SystemInfoModel(
        os=platform.system(),
        os_release=platform.release(),
        architecture=platform.machine(),
        python_version=sys.version,
        cpu_count=os.cpu_count(),
        process_id=os.getpid(),
        uptime_seconds=round(time.time() - _SERVER_START_TIME, 2),
        session_id=session.session_id,
        scenario_id=session.scenario_id,
        server_id=session.server_id,
        metrics=metrics,
    )
    return info.model_dump()


def calculate(
    expression: Annotated[str, Field(description="Mathematical expression string to evaluate (e.g. '3 + 5 * (10 - 2)')")]
) -> Dict[str, Any] | ToolResult:
    """Safely evaluate basic mathematical expressions (e.g. '3 + 5 * (10 - 2)')."""
    tb = check_scenario_tool_behavior("calculate")
    if tb is not None:
        return tb

    session = session_manager.get_session()
    allowed_operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.Mod: operator.mod,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        elif isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            op_type = type(node.op)
            if op_type in allowed_operators:
                return allowed_operators[op_type](left, right)
            raise ValueError(f"Unsupported binary operator: {op_type.__name__}")
        elif isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            op_type = type(node.op)
            if op_type in allowed_operators:
                return allowed_operators[op_type](operand)
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        else:
            raise ValueError(f"Unsupported expression node: {type(node).__name__}")

    try:
        parsed = ast.parse(expression, mode="eval")
        result = _eval(parsed.body)
        return {
            "expression": expression,
            "result": result,
            "status": "success",
            "session_id": session.session_id,
            "scenario_id": session.scenario_id,
            "server_id": session.server_id,
        }
    except Exception as e:
        return ToolResult(
            content=f"Calculation Error: {e}",
            is_error=True
        )


def manage_users(
    action: Annotated[str, Field(description="User CRUD action ('list', 'get', 'create', 'delete')")],
    user_id: Annotated[Optional[str], Field(default=None, description="Unique User ID (required for 'get' and 'delete' actions)")] = None,
    name: Annotated[Optional[str], Field(default=None, description="Full Name (required for 'create' action)")] = None,
    email: Annotated[Optional[str], Field(default=None, description="Email address (required for 'create' action)")] = None,
    role: Annotated[str, Field(default="user", description="User access role (default: 'user')")] = "user",
    session_id: Annotated[Optional[str], Field(default=None, description="Optional session ID context override")] = None,
    scenario_id: Annotated[Optional[str], Field(default=None, description="Optional scenario ID context override")] = None,
    server_id: Annotated[Optional[str], Field(default=None, description="Optional server ID context override")] = None,
) -> Dict[str, Any] | ToolResult:
    """
    Session & Scenario isolated CRUD operations for user management.
    Actions: 'list', 'get', 'create', 'delete'.
    """
    tb = check_scenario_tool_behavior("manage_users", session_id, scenario_id, server_id)
    if tb is not None:
        return tb

    session = session_manager.get_session(session_id, scenario_id, server_id)
    user_db = session.user_db

    action = action.lower()
    if action == "list":
        return {
            "session_id": session.session_id,
            "scenario_id": session.scenario_id,
            "server_id": session.server_id,
            "users": list(user_db.values()),
            "count": len(user_db),
        }
    elif action == "get":
        if not user_id:
            return ToolResult(content="user_id is required for 'get' action", is_error=True)
        user = user_db.get(user_id)
        if not user:
            return ToolResult(content=f"User with id '{user_id}' not found", is_error=True)
        return {
            "session_id": session.session_id,
            "scenario_id": session.scenario_id,
            "server_id": session.server_id,
            "user": user,
        }
    elif action == "create":
        if not name or not email:
            return ToolResult(content="'name' and 'email' are required for 'create' action", is_error=True)
        new_id = user_id or f"user_{uuid.uuid4().hex[:6]}"
        try:
            user_model = UserRecord(id=new_id, name=name, email=email, role=role)
            user_db[new_id] = user_model.model_dump()
            return {
                "status": "created",
                "session_id": session.session_id,
                "scenario_id": session.scenario_id,
                "server_id": session.server_id,
                "user": user_db[new_id],
            }
        except Exception as exc:
            return ToolResult(content=f"Pydantic user validation failed: {exc}", is_error=True)
    elif action == "delete":
        if not user_id:
            return ToolResult(content="user_id is required for 'delete' action", is_error=True)
        deleted = user_db.pop(user_id, None)
        if not deleted:
            return ToolResult(content=f"User with id '{user_id}' not found", is_error=True)
        return {
            "status": "deleted",
            "session_id": session.session_id,
            "scenario_id": session.scenario_id,
            "server_id": session.server_id,
            "user": deleted,
        }

    return ToolResult(content=f"Invalid action '{action}'. Supported actions: list, get, create, delete.", is_error=True)


def http_ping(
    url: Annotated[str, Field(description="Target URL to ping (e.g. 'https://httpbin.org/get')")],
    timeout_seconds: Annotated[float, Field(default=5.0, description="HTTP request timeout in seconds (default: 5.0)")] = 5.0,
) -> Dict[str, Any] | ToolResult:
    """Perform a real HTTP request to test connectivity and retrieve headers/status."""
    tb = check_scenario_tool_behavior("http_ping")
    if tb is not None:
        return tb

    session = session_manager.get_session()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    start = time.time()
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mock-MCP-Server/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            latency = round((time.time() - start) * 1000, 2)
            headers = dict(response.headers)
            return {
                "url": url,
                "status_code": response.status,
                "latency_ms": latency,
                "headers": headers,
                "success": True,
                "session_id": session.session_id,
                "scenario_id": session.scenario_id,
                "server_id": session.server_id,
            }
    except Exception as e:
        return ToolResult(
            content=f"HTTP Ping Failed for '{url}': {e}",
            is_error=True
        )


def generate_random_data(
    type_name: Annotated[str, Field(default="uuid", description="Data generation type ('uuid', 'hex', 'timestamp')")] = "uuid",
    count: Annotated[int, Field(default=1, description="Number of items to generate (1 to 50, default: 1)")] = 1,
) -> Dict[str, Any] | ToolResult:
    """
    Generate mock real data.
    type_name options: 'uuid', 'hex', 'timestamp'
    """
    tb = check_scenario_tool_behavior("generate_random_data")
    if tb is not None:
        return tb

    session = session_manager.get_session()
    count = max(1, min(count, 50))
    results = []
    for _ in range(count):
        if type_name == "uuid":
            results.append(str(uuid.uuid4()))
        elif type_name == "hex":
            results.append(uuid.uuid4().hex)
        elif type_name == "timestamp":
            results.append(datetime.now(timezone.utc).isoformat())
        else:
            return ToolResult(content=f"Unsupported type '{type_name}'. Choose from 'uuid', 'hex', 'timestamp'.", is_error=True)

    return {
        "type": type_name,
        "count": len(results),
        "items": results,
        "session_id": session.session_id,
        "scenario_id": session.scenario_id,
        "server_id": session.server_id,
    }


# --- Dynamic Tool Discovery Meta-Tools ---

def find_tools(
    query: Annotated[str, Field(description="Natural language query or intent describing what tool functionality is needed")],
    top_k: Annotated[int, Field(default=3, description="Maximum number of relevant tool suggestions to return")] = 3,
) -> Dict[str, Any]:
    """
    Dynamic Tool Discovery Meta-Tool: Perform Progressive/Semantic Search over catalog schemas.
    Drastically reduces LLM context window token overhead from >30k tokens down to ~1,300 tokens.
    """
    from mock_mcp_gateway.schemas import schema_registry

    query_words = set(re.findall(r"\w+", query.lower()))
    all_tools = schema_registry.tools

    scored = []
    for name, tool_def in all_tools.items():
        desc = tool_def.description.lower()
        desc_words = set(re.findall(r"\w+", desc))
        intersection = len(query_words.intersection(desc_words))
        name_match = 2.0 if any(qw in name.lower() for qw in query_words) else 0.0
        score = intersection + name_match
        scored.append((score, tool_def))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_matches = scored[:top_k]

    results = []
    for score, tool_def in top_matches:
        results.append({
            "name": tool_def.name,
            "description": tool_def.description,
            "relevance_score": score,
            "usage_hint": f"Call describe_tools(['{tool_def.name}']) to inspect complete JSON inputSchema.",
        })

    session = session_manager.get_session()
    return {
        "query": query,
        "matches_found": len(results),
        "tools": results,
        "session_id": session.session_id,
    }


def describe_tools(
    tool_names: Annotated[List[str], Field(description="List of tool names to fetch complete JSON input schemas for")]
) -> Dict[str, Any]:
    """
    Dynamic Tool Discovery Meta-Tool: Retrieve complete JSON input schemas for specific tools on demand.
    """
    from mock_mcp_gateway.schemas import schema_registry

    matched_schemas = []
    for name in tool_names:
        tool_def = schema_registry.get_tool(name)
        if tool_def:
            matched_schemas.append(tool_def.to_mcp_format())

    session = session_manager.get_session()
    return {
        "count": len(matched_schemas),
        "tools": matched_schemas,
        "session_id": session.session_id,
    }


# --- Code Mode Execution Meta-Tools ---

def list_tool_files() -> Dict[str, Any]:
    """
    Code Mode Meta-Tool: List available tools as virtual file paths for script inspect & execution.
    """
    from mock_mcp_gateway.schemas import schema_registry

    files = [f"/tools/{name}.py" for name in schema_registry.tools.keys()]
    session = session_manager.get_session()
    return {
        "tool_files": files,
        "count": len(files),
        "session_id": session.session_id,
    }


def read_tool_file(
    path: Annotated[str, Field(description="Virtual tool file path (e.g. '/tools/manage_users.py')")]
) -> Dict[str, Any] | ToolResult:
    """
    Code Mode Meta-Tool: Inspect functional signatures and documentation for a virtual tool file.
    """
    from mock_mcp_gateway.schemas import schema_registry

    tool_name = path.split("/")[-1].replace(".py", "")
    tool_def = schema_registry.get_tool(tool_name)
    if not tool_def:
        return ToolResult(content=f"Tool file '{path}' not found.", is_error=True)

    session = session_manager.get_session()
    return {
        "path": path,
        "name": tool_def.name,
        "description": tool_def.description,
        "schema": tool_def.inputSchema.model_dump(),
        "session_id": session.session_id,
    }


def get_tool_docs(
    tool_name: Annotated[str, Field(description="Tool name to retrieve markdown documentation for")]
) -> Dict[str, Any] | ToolResult:
    """
    Code Mode Meta-Tool: Retrieve detailed functional documentation for a tool.
    """
    from mock_mcp_gateway.schemas import schema_registry

    tool_def = schema_registry.get_tool(tool_name)
    if not tool_def:
        return ToolResult(content=f"Tool '{tool_name}' not found.", is_error=True)

    session = session_manager.get_session()
    docs = f"# {tool_def.name}\n\n{tool_def.description}\n\n## Input Schema\n```json\n{json.dumps(tool_def.inputSchema.model_dump(), indent=2)}\n```"
    return {
        "tool_name": tool_name,
        "documentation": docs,
        "session_id": session.session_id,
    }


def execute_tool_code(
    code: Annotated[str, Field(description="Python script snippet to execute in Gateway sandbox to filter/transform data")]
) -> Dict[str, Any] | ToolResult:
    """
    Code Mode Meta-Tool: Execute Python script snippet directly inside Gateway Sandbox.
    Filters, transforms, or paginates multi-step tool calls at the Gateway, returning only compact result to LLM context (92.8% token savings).
    """
    session = session_manager.get_session()
    user_db = session.user_db

    # Restrict execution environment globals
    safe_globals = {
        "__builtins__": {
            "len": len,
            "list": list,
            "dict": dict,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "sum": sum,
            "min": min,
            "max": max,
            "range": range,
            "sorted": sorted,
        },
        "user_db": user_db,
        "session_id": session.session_id,
        "scenario_id": session.scenario_id,
    }
    local_vars: Dict[str, Any] = {}

    try:
        exec(code, safe_globals, local_vars)
        output = local_vars.get("result", local_vars)
        return {
            "status": "success",
            "result": output,
            "session_id": session.session_id,
        }
    except Exception as exc:
        return ToolResult(
            content=f"Code Mode Execution Error: {exc}",
            is_error=True
        )


# --- Debugging & Real-World Reproduction Meta-Tools ---

def get_execution_trace(
    session_id: Annotated[Optional[str], Field(default=None, description="Session ID to retrieve trace for (defaults to active session)")] = None
) -> Dict[str, Any]:
    """
    Real-World Debug Meta-Tool: Retrieve chronological execution trace of tool calls for a session.
    Inspect call parameters, outputs, timing, state hashes, and errors.
    """
    from mock_mcp_gateway.recorder import trace_recorder

    session = session_manager.get_session(session_id=session_id)
    records = trace_recorder.get_session_trace(session.session_id)
    return {
        "session_id": session.session_id,
        "count": len(records),
        "trace": [r.model_dump() for r in records],
    }


def inject_fault_rule(
    scenario_id: Annotated[str, Field(default="*", description="Target scenario ID or '*' for all scenarios")] = "*",
    tool_name: Annotated[str, Field(default="*", description="Target tool name or fnmatch pattern (e.g. 'manage_*')")] = "*",
    fault_type: Annotated[str, Field(default="latency", description="Fault type ('latency', 'error_rate', 'http_status', 'malformed_json', 'schema_drift', 'timeout')")] = "latency",
    rate: Annotated[float, Field(default=1.0, ge=0.0, le=1.0, description="Probability rate (0.0 to 1.0)")] = 1.0,
    delay_ms: Annotated[float, Field(default=0.0, ge=0.0, description="Simulated latency delay in milliseconds")] = 0.0,
    error_message: Annotated[str, Field(default="Simulated chaos fault", description="Error message text")] = "Simulated chaos fault",
    http_status: Annotated[Optional[int], Field(default=None, description="HTTP status code (e.g. 429, 502, 503, 504)")] = None,
) -> Dict[str, Any]:
    """
    Real-World Debug Meta-Tool: Dynamically configure fault injection to reproduce flaky, edge-case, or transient bugs.
    """
    from mock_mcp_gateway.chaos import FaultRule, FaultType, chaos_engine

    try:
        ftype = FaultType(fault_type)
    except ValueError:
        ftype = FaultType.LATENCY

    rule = FaultRule(
        scenario_id=scenario_id,
        tool_name=tool_name,
        fault_type=ftype,
        rate=rate,
        delay_ms=delay_ms,
        error_message=error_message,
        http_status=http_status,
    )
    rule_id = chaos_engine.add_rule(rule)
    return {
        "status": "active",
        "rule_id": rule_id,
        "rule": rule.model_dump(),
    }


def clear_fault_rules(
    scenario_id: Annotated[Optional[str], Field(default=None, description="Target scenario ID to clear rules for, or None for all")] = None
) -> Dict[str, Any]:
    """
    Real-World Debug Meta-Tool: Clear active fault injection rules.
    """
    from mock_mcp_gateway.chaos import chaos_engine

    cleared = chaos_engine.clear_rules(scenario_id=scenario_id)
    return {
        "status": "cleared",
        "scenario_id": scenario_id or "all",
        "cleared_count": cleared,
    }


def create_session_snapshot(
    label: Annotated[str, Field(default="", description="Human readable label for the state snapshot")] = ""
) -> Dict[str, Any]:
    """
    Real-World Debug Meta-Tool: Take a time-travel snapshot checkpoint of current session state.
    """
    session = session_manager.get_session()
    snap_id = session.create_snapshot(label=label)
    return {
        "status": "created",
        "session_id": session.session_id,
        "snapshot_id": snap_id,
        "label": label or snap_id,
    }


def restore_session_snapshot(
    snapshot_id: Annotated[str, Field(description="Snapshot ID to restore session state to")]
) -> Dict[str, Any] | ToolResult:
    """
    Real-World Debug Meta-Tool: Time travel rollback session state to a previously saved snapshot.
    """
    session = session_manager.get_session()
    ok = session.restore_snapshot(snapshot_id)
    if not ok:
        return ToolResult(content=f"Snapshot '{snapshot_id}' not found for session '{session.session_id}'", is_error=True)

    return {
        "status": "restored",
        "session_id": session.session_id,
        "snapshot_id": snapshot_id,
        "current_state": session.state,
    }


def diff_session_snapshots(
    snapshot1_id: Annotated[str, Field(description="First snapshot ID")],
    snapshot2_id: Annotated[str, Field(description="Second snapshot ID")],
) -> Dict[str, Any]:
    """
    Real-World Debug Meta-Tool: Compare state difference between two snapshots.
    """
    session = session_manager.get_session()
    diff = session.diff_snapshots(snapshot1_id, snapshot2_id)
    return {
        "session_id": session.session_id,
        "snapshot1_id": snapshot1_id,
        "snapshot2_id": snapshot2_id,
        "diff": diff,
    }


def export_repro_bundle(
    session_id: Annotated[Optional[str], Field(default=None, description="Session ID to export repro bundle for")] = None
) -> Dict[str, Any]:
    """
    Real-World Debug Meta-Tool: Export a self-contained ReproBundle (.mcp-repro.json) for sharing, bug reports, and CI replay.
    """
    from mock_mcp_gateway.chaos import chaos_engine
    from mock_mcp_gateway.recorder import trace_recorder

    session = session_manager.get_session(session_id=session_id)
    faults = chaos_engine.list_rules(scenario_id=session.scenario_id)

    bundle = trace_recorder.export_repro_bundle(
        session_id=session.session_id,
        scenario_id=session.scenario_id,
        initial_state=session.state,
        fault_rules=faults,
    )
    return bundle.model_dump()


def replay_session_trace(
    repro_bundle_json: Annotated[str, Field(description="Serialized JSON string or object of a ReproBundle")]
) -> Dict[str, Any] | ToolResult:
    """
    Real-World Debug Meta-Tool: Deterministically replay a recorded trace bundle and return a drift analysis report.
    """
    from mock_mcp_gateway.recorder import ReplayEngine, ReproBundle

    try:
        if isinstance(repro_bundle_json, str):
            data = json.loads(repro_bundle_json)
        else:
            data = repro_bundle_json
        bundle = ReproBundle.model_validate(data)
    except Exception as exc:
        return ToolResult(content=f"Failed to parse ReproBundle: {exc}", is_error=True)

    # Local dummy executor for tool replay test
    async def _mock_executor(sc_id, tool_name, args):
        return {"status": "replayed", "tool": tool_name}

    import asyncio
    report = asyncio.run(ReplayEngine.replay_bundle(bundle, _mock_executor))
    return report.model_dump()


# --- Resource Functions ---

def system_status_resource() -> str:
    """Return real-time JSON status of the Mock MCP server."""
    session = session_manager.get_session()
    status = {
        "status": "healthy",
        "session_id": session.session_id,
        "scenario_id": session.scenario_id,
        "server_id": session.server_id,
        "uptime_seconds": round(time.time() - _SERVER_START_TIME, 2),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "registered_users": len(session.user_db),
        "metrics": session.state.get("system_info"),
    }
    return json.dumps(status, indent=2)


def users_list_resource() -> str:
    """Return the current list of session-isolated users as JSON formatted string."""
    session = session_manager.get_session()
    return json.dumps(list(session.user_db.values()), indent=2)


# --- Prompt Functions ---

def code_review_prompt(language: str, code: str, focus: str = "performance and security") -> str:
    """Generate a prompt for reviewing code."""
    return f"""Please perform a thorough code review for the following {language} snippet.

Focus Areas: {focus}

Code:
```{language}
{code}
```

Provide constructive feedback and improvement suggestions.
"""
