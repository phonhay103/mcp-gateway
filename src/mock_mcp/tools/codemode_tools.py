"""
Code Mode Execution Meta-Tools (execute_tool_code, list_tool_files, read_tool_file, get_tool_docs).
Executes script logic inside Gateway sandbox, compressing tool outputs (92.8% token savings).
"""

import json
from typing import Annotated, Any, Dict
from fastmcp.tools.base import ToolResult
from pydantic import Field

from mock_mcp.core.session import session_manager


def list_tool_files() -> Dict[str, Any]:
    """Code Mode Meta-Tool: List available tools as virtual file paths for script inspect & execution."""
    from mock_mcp.schemas import schema_registry

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
    """Code Mode Meta-Tool: Inspect functional signatures and documentation for a virtual tool file."""
    from mock_mcp.schemas import schema_registry

    tool_name = path.split("/")[-1].replace(".py", "")
    tool_def = schema_registry.get_tool(tool_name)
    if not tool_def:
        return ToolResult(content=f"Tool file '{path}' not found.", is_error=True)

    session = session_manager.get_session()
    return {
        "path": path,
        "name": tool_def.name,
        "description": tool_def.description,
        "schema": tool_def.inputSchema,
        "session_id": session.session_id,
    }


def get_tool_docs(
    tool_name: Annotated[str, Field(description="Tool name to retrieve markdown documentation for")]
) -> Dict[str, Any] | ToolResult:
    """Code Mode Meta-Tool: Retrieve detailed functional documentation for a tool."""
    from mock_mcp.schemas import schema_registry

    tool_def = schema_registry.get_tool(tool_name)
    if not tool_def:
        return ToolResult(content=f"Tool '{tool_name}' not found.", is_error=True)

    session = session_manager.get_session()
    docs = f"# {tool_def.name}\n\n{tool_def.description}\n\n## Input Schema\n```json\n{json.dumps(tool_def.inputSchema, indent=2)}\n```"
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
        return ToolResult(content=f"Code Mode Execution Error: {exc}", is_error=True)
