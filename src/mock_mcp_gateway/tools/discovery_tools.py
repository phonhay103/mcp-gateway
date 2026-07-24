"""
Dynamic Tool Discovery Meta-Tools (find_tools, describe_tools).
Reduces initial LLM context window token bloat from >30k tokens down to ~1.3k tokens.
"""

import re
from typing import Annotated, Any, Dict, List
from pydantic import Field

from mock_mcp_gateway.core.session import session_manager


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
