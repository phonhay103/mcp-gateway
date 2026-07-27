"""
Core functional tools provided by the Gateway (echo, calculate, manage_users, etc.).
"""

from datetime import datetime, timezone
import ast
import json
import operator
import os
import platform
import sys
import time
import urllib.request
from typing import Annotated, Any, Dict, Optional
from fastmcp.tools.base import ToolResult
from pydantic import BaseModel, Field

from mock_mcp.core.session import session_manager

_SERVER_START_TIME = time.time()


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
    from mock_mcp.chaos import chaos_engine

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
        rule = session.get_error_simulation(tool_name)
        if rule:
            message = rule.get(
                "message",
                f"MCP Error simulated for tool '{tool_name}' in scenario '{session.scenario_id}'",
            )
            is_err = rule.get("is_error", True)
            return ToolResult(content=message, is_error=is_err)
        return None

    # Check tool-level error definition
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

    # Check tool-level mock result override
    mock_res = behavior.get("mock_result")
    if mock_res and isinstance(mock_res, dict):
        res = dict(mock_res)
        res["_is_mock"] = True
        res["session_id"] = session.session_id
        res["scenario_id"] = session.scenario_id
        res["server_id"] = session.server_id
        return res

    return None


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
        return ToolResult(content=f"Calculation Error: {e}", is_error=True)


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
    """Session & Scenario isolated CRUD operations for user management."""
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
        import uuid
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
        req = urllib.request.Request(url, headers={"User-Agent": "Mock-MCP-Server/1.0"})
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
        return ToolResult(content=f"HTTP Ping Failed for '{url}': {e}", is_error=True)


def generate_random_data(
    type_name: Annotated[str, Field(default="uuid", description="Data generation type ('uuid', 'hex', 'timestamp')")] = "uuid",
    count: Annotated[int, Field(default=1, description="Number of items to generate (1 to 50, default: 1)")] = 1,
) -> Dict[str, Any] | ToolResult:
    """Generate mock real data. type_name options: 'uuid', 'hex', 'timestamp'."""
    tb = check_scenario_tool_behavior("generate_random_data")
    if tb is not None:
        return tb

    import uuid
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
