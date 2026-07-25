"""
MCP 2026-07-28 Streamable HTTP Header Enforcement Middleware.
"""

from typing import Any, Optional
from starlette.requests import Request
from starlette.responses import JSONResponse


def validate_mcp_headers(request: Request, method: str, req_id: Any, params: dict) -> Optional[JSONResponse]:
    """Validate MCP 2026-07-28 Streamable HTTP headers (Mcp-Method, Mcp-Name)."""
    # 1. Mcp-Method header check
    mcp_method_header = request.headers.get("mcp-method")
    if mcp_method_header and mcp_method_header != method:
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32602,
                "message": f"Header 'Mcp-Method' ({mcp_method_header}) does not match request body method ({method})"
            },
        }, status_code=400)

    # 2. Mcp-Name header check for tools/call
    if method == "tools/call":
        mcp_name_header = request.headers.get("mcp-name")
        raw_tool_name = params.get("name")
        if mcp_name_header and raw_tool_name and mcp_name_header != raw_tool_name:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32602,
                    "message": f"Header 'Mcp-Name' ({mcp_name_header}) does not match tool name in params ({raw_tool_name})"
                },
            }, status_code=400)

    return None
