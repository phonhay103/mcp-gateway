"""
Registry mapping tools, resources, and prompts for dynamic server construction.
"""

from typing import Any, Callable, Dict, List
from fastmcp import FastMCP
from mock_mcp_gateway import tools

AVAILABLE_TOOLS: Dict[str, Callable[..., Any]] = {
    "echo": tools.echo,
    "get_system_info": tools.get_system_info,
    "calculate": tools.calculate,
    "manage_users": tools.manage_users,
    "http_ping": tools.http_ping,
    "generate_random_data": tools.generate_random_data,
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
