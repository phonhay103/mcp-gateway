"""
Tool, resource, and prompt implementations for Mock MCP using Pydantic v2 schemas and 3-Way Session Isolation.
MCP Tools remain scenario-agnostic pure functions operating on isolated session state.
Tool parameter schemas are annotated via typing.Annotated & Pydantic Field for FastMCP native tools/list protocol serving.
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
import uuid
from typing import Annotated, Any, Dict, List, Optional
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


# --- Tool Functions (Scenario-Agnostic with Annotated FastMCP Schemas) ---

def echo(
    message: Annotated[str, Field(description="Message string to echo back")]
) -> Dict[str, Any]:
    """Echo back the provided message along with helpful string metadata and active session context."""
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


def get_system_info() -> Dict[str, Any]:
    """Retrieve system runtime information combined with session fixture state."""
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
) -> Dict[str, Any]:
    """Safely evaluate basic mathematical expressions (e.g. '3 + 5 * (10 - 2)')."""
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
        return {
            "expression": expression,
            "error": str(e),
            "status": "error",
            "session_id": session.session_id,
            "scenario_id": session.scenario_id,
            "server_id": session.server_id,
        }


def manage_users(
    action: Annotated[str, Field(description="User CRUD action ('list', 'get', 'create', 'delete')")],
    user_id: Annotated[Optional[str], Field(default=None, description="Unique User ID (required for 'get' and 'delete' actions)")] = None,
    name: Annotated[Optional[str], Field(default=None, description="Full Name (required for 'create' action)")] = None,
    email: Annotated[Optional[str], Field(default=None, description="Email address (required for 'create' action)")] = None,
    role: Annotated[str, Field(default="user", description="User access role (default: 'user')")] = "user",
    session_id: Annotated[Optional[str], Field(default=None, description="Optional session ID context override")] = None,
    scenario_id: Annotated[Optional[str], Field(default=None, description="Optional scenario ID context override")] = None,
    server_id: Annotated[Optional[str], Field(default=None, description="Optional server ID context override")] = None,
) -> Dict[str, Any]:
    """
    Session & Scenario isolated CRUD operations for user management.
    Actions: 'list', 'get', 'create', 'delete'.
    """
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
            return {"error": "user_id is required for 'get' action"}
        user = user_db.get(user_id)
        if not user:
            return {"error": f"User with id '{user_id}' not found"}
        return {
            "session_id": session.session_id,
            "scenario_id": session.scenario_id,
            "server_id": session.server_id,
            "user": user,
        }
    elif action == "create":
        if not name or not email:
            return {"error": "'name' and 'email' are required for 'create' action"}
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
            return {"error": f"Pydantic user validation failed: {exc}"}
    elif action == "delete":
        if not user_id:
            return {"error": "user_id is required for 'delete' action"}
        deleted = user_db.pop(user_id, None)
        if not deleted:
            return {"error": f"User with id '{user_id}' not found"}
        return {
            "status": "deleted",
            "session_id": session.session_id,
            "scenario_id": session.scenario_id,
            "server_id": session.server_id,
            "user": deleted,
        }

    return {"error": f"Invalid action '{action}'. Supported actions: list, get, create, delete."}


def http_ping(
    url: Annotated[str, Field(description="Target URL to ping (e.g. 'https://httpbin.org/get')")],
    timeout_seconds: Annotated[float, Field(default=5.0, description="HTTP request timeout in seconds (default: 5.0)")] = 5.0,
) -> Dict[str, Any]:
    """Perform a real HTTP request to test connectivity and retrieve headers/status."""
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
        latency = round((time.time() - start) * 1000, 2)
        return {
            "url": url,
            "error": str(e),
            "latency_ms": latency,
            "success": False,
            "session_id": session.session_id,
            "scenario_id": session.scenario_id,
            "server_id": session.server_id,
        }


def generate_random_data(
    type_name: Annotated[str, Field(default="uuid", description="Data generation type ('uuid', 'hex', 'timestamp')")] = "uuid",
    count: Annotated[int, Field(default=1, description="Number of items to generate (1 to 50, default: 1)")] = 1,
) -> Dict[str, Any]:
    """
    Generate mock real data.
    type_name options: 'uuid', 'hex', 'timestamp'
    """
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
            return {"error": f"Unsupported type '{type_name}'. Choose from 'uuid', 'hex', 'timestamp'."}

    return {
        "type": type_name,
        "count": len(results),
        "items": results,
        "session_id": session.session_id,
        "scenario_id": session.scenario_id,
        "server_id": session.server_id,
    }


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
