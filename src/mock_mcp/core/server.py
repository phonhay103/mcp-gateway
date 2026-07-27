"""
Server factory and default server instance.
"""

from fastmcp import FastMCP
from mock_mcp.core.registry import register_features


def create_mcp_server(
    name: str = "Mock MCP Server",
    description: str | None = "A mock Model Context Protocol (MCP) server running real functions.",
    tools: list[str] | None = None,
    resources: list[str] | None = None,
    prompts: list[str] | None = None,
) -> FastMCP:
    """Factory function to create a configured FastMCP instance."""
    mcp_instance = FastMCP(
        name=name,
        instructions=description or "",
    )

    # If no specific features selected, register all by default
    if tools is None and resources is None and prompts is None:
        tools = ["*"]
        resources = ["*"]
        prompts = ["*"]

    register_features(
        mcp_instance=mcp_instance,
        tool_names=tools,
        resource_names=resources,
        prompt_names=prompts,
    )

    return mcp_instance


# Default server instance containing all features
mcp = create_mcp_server()
