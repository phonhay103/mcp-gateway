"""
Registry mapping tools, meta-tools, resources, and prompts for dynamic server construction.
"""

from typing import Any, Callable, Dict, List
from fastmcp import FastMCP
from mock_mcp_gateway import tools

AVAILABLE_TOOLS: Dict[str, Callable[..., Any]] = {
    # Core Tools
    "echo": tools.echo,
    "get_system_info": tools.get_system_info,
    "calculate": tools.calculate,
    "manage_users": tools.manage_users,
    "http_ping": tools.http_ping,
    "generate_random_data": tools.generate_random_data,
    # Dynamic Tool Discovery Meta-Tools
    "find_tools": tools.find_tools,
    "describe_tools": tools.describe_tools,
    # Code Mode Execution Meta-Tools
    "list_tool_files": tools.list_tool_files,
    "read_tool_file": tools.read_tool_file,
    "get_tool_docs": tools.get_tool_docs,
    "execute_tool_code": tools.execute_tool_code,
    # Real-World Debugging & Reproduction Meta-Tools
    "get_execution_trace": tools.get_execution_trace,
    "inject_fault_rule": tools.inject_fault_rule,
    "clear_fault_rules": tools.clear_fault_rules,
    "create_session_snapshot": tools.create_session_snapshot,
    "restore_session_snapshot": tools.restore_session_snapshot,
    "diff_session_snapshots": tools.diff_session_snapshots,
    "export_repro_bundle": tools.export_repro_bundle,
    "replay_session_trace": tools.replay_session_trace,
}

AVAILABLE_RESOURCES: Dict[str, Callable[..., Any]] = {
    "system://status": tools.system_status_resource,
    "users://list": tools.users_list_resource,
}

AVAILABLE_PROMPTS: Dict[str, Callable[..., Any]] = {
    "code_review": tools.code_review_prompt,
}


def register_features(
    mcp_instance: FastMCP,
    tool_names: List[str] | None = None,
    resource_names: List[str] | None = None,
    prompt_names: List[str] | None = None,
) -> None:
    """
    Register specified tools, resources, and prompts onto a FastMCP instance.
    Supports '*' wildcard to attach all available features.
    """
    # 1. Register Tools
    if tool_names:
        if "*" in tool_names:
            selected_tools = list(AVAILABLE_TOOLS.keys())
        else:
            selected_tools = tool_names

        for name in selected_tools:
            if name in AVAILABLE_TOOLS:
                mcp_instance.tool()(AVAILABLE_TOOLS[name])
            else:
                raise ValueError(f"Unknown tool '{name}'. Available: {list(AVAILABLE_TOOLS.keys())}")

    # 2. Register Resources
    if resource_names:
        if "*" in resource_names:
            selected_resources = list(AVAILABLE_RESOURCES.keys())
        else:
            selected_resources = resource_names

        for uri in selected_resources:
            if uri in AVAILABLE_RESOURCES:
                mcp_instance.resource(uri)(AVAILABLE_RESOURCES[uri])
            else:
                raise ValueError(f"Unknown resource '{uri}'. Available: {list(AVAILABLE_RESOURCES.keys())}")

    # 3. Register Prompts
    if prompt_names:
        if "*" in prompt_names:
            selected_prompts = list(AVAILABLE_PROMPTS.keys())
        else:
            selected_prompts = prompt_names

        for name in selected_prompts:
            if name in AVAILABLE_PROMPTS:
                mcp_instance.prompt(name)(AVAILABLE_PROMPTS[name])
            else:
                raise ValueError(f"Unknown prompt '{name}'. Available: {list(AVAILABLE_PROMPTS.keys())}")
