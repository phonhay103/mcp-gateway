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

from mock_mcp.core.context import current_scenario_id, scenario_states

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
    scenario_id: str
    metrics: Optional[Dict[str, Any]] = None


class EchoOutput(BaseModel):
    """Pydantic model for Echo tool output."""
    original_message: str
    length: int
    reversed: str
    uppercase: str
    timestamp_utc: str
    scenario_id: str


def check_scenario_tool_behavior(
    tool_name: str,
) -> Optional[ToolResult | Dict[str, Any]]:
    """
    Check if the active scenario defines a tool-level result override or MCP error definition (isError: true).
    """
    from mock_mcp.core.context import current_scenario_id, scenario_configs_map

    sc_id = current_scenario_id.get()
    sc = scenario_configs_map.get(sc_id)
    if not sc:
        return None

    # Check tool-level overrides defined in tool_behaviors
    behavior = sc.tool_behaviors.get(tool_name) if sc.tool_behaviors else None
    if behavior:
        # Apply Tool-level Chaos Fault Injection if configured
        if behavior.chaos:
            import time
            import random

            # 1. Latency injection
            if behavior.chaos.latency_ms > 0:
                time.sleep(behavior.chaos.latency_ms / 1000.0)

            # 2. Random error rate injection
            if behavior.chaos.error_rate > 0.0:
                if random.random() < behavior.chaos.error_rate:
                    return ToolResult(
                        content=f"[Chaos] Simulated random error for tool '{tool_name}' (rate={behavior.chaos.error_rate})",
                        is_error=True
                    )

            # 3. HTTP status code simulation
            if behavior.chaos.http_status is not None:
                return ToolResult(
                    content=f"[Chaos] Simulated HTTP status error {behavior.chaos.http_status} for tool '{tool_name}'",
                    meta={"code": behavior.chaos.http_status},
                    is_error=True
                )

        # Check tool-level error definition
        if behavior.error:
            err = behavior.error
            if err.is_error:
                meta = {}
                if err.code is not None:
                    meta["code"] = err.code
                if err.details:
                    meta["details"] = err.details
                formatted_msg = f"[Error Code {err.code}] {err.message}" if err.code is not None else err.message
                return ToolResult(
                    content=formatted_msg,
                    meta=meta if meta else None,
                    is_error=True,
                )

        # Check tool-level mock result override
        if behavior.mock_result is not None:
            res = dict(behavior.mock_result)
            res["_is_mock"] = True
            res["scenario_id"] = sc_id
            return res

    return None


def echo(
    message: Annotated[str, Field(description="Message string to echo back")]
) -> Dict[str, Any] | ToolResult:
    """Echo back the provided message along with helpful string metadata and active scenario context."""
    tb = check_scenario_tool_behavior("echo")
    if tb is not None:
        return tb

    sc_id = current_scenario_id.get()
    out = EchoOutput(
        original_message=message,
        length=len(message),
        reversed=message[::-1],
        uppercase=message.upper(),
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        scenario_id=sc_id,
    )
    return out.model_dump()


def get_system_info() -> Dict[str, Any] | ToolResult:
    """Retrieve system runtime information combined with active scenario state."""
    tb = check_scenario_tool_behavior("get_system_info")
    if tb is not None:
        return tb

    sc_id = current_scenario_id.get()
    state = scenario_states.get(sc_id, {})
    metrics = state.get("system_info")

    info = SystemInfoModel(
        os=platform.system(),
        os_release=platform.release(),
        architecture=platform.machine(),
        python_version=sys.version,
        cpu_count=os.cpu_count(),
        process_id=os.getpid(),
        uptime_seconds=round(time.time() - _SERVER_START_TIME, 2),
        scenario_id=sc_id,
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

    sc_id = current_scenario_id.get()
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
            "scenario_id": sc_id,
        }
    except Exception as e:
        return ToolResult(content=f"Calculation Error: {e}", is_error=True)


def manage_users(
    action: Annotated[str, Field(description="User CRUD action ('list', 'get', 'create', 'delete')")],
    user_id: Annotated[Optional[str], Field(default=None, description="Unique User ID (required for 'get' and 'delete' actions)")] = None,
    name: Annotated[Optional[str], Field(default=None, description="Full Name (required for 'create' action)")] = None,
    email: Annotated[Optional[str], Field(default=None, description="Email address (required for 'create' action)")] = None,
    role: Annotated[str, Field(default="user", description="User access role (default: 'user')")] = "user",
) -> Dict[str, Any] | ToolResult:
    """Scenario isolated CRUD operations for user management."""
    tb = check_scenario_tool_behavior("manage_users")
    if tb is not None:
        return tb

    sc_id = current_scenario_id.get()
    if sc_id not in scenario_states:
        scenario_states[sc_id] = {}
    if "users" not in scenario_states[sc_id]:
        scenario_states[sc_id]["users"] = {}
    user_db = scenario_states[sc_id]["users"]

    action = action.lower()
    if action == "list":
        return {
            "scenario_id": sc_id,
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
            "scenario_id": sc_id,
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
                "scenario_id": sc_id,
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
            "scenario_id": sc_id,
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

    sc_id = current_scenario_id.get()
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
                "scenario_id": sc_id,
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
    sc_id = current_scenario_id.get()
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
        "scenario_id": sc_id,
    }
