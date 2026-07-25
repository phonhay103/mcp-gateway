"""
Chaos and Fault Injection Middleware.
"""

from typing import Any, Optional
from starlette.responses import JSONResponse
from mock_mcp_gateway.core.chaos import chaos_engine


def evaluate_chaos(scenario_id: str, tool_name: str, tool_args: dict, req_id: Any) -> Optional[JSONResponse]:
    """Evaluate and apply chaos fault injection if triggered."""
    fault = chaos_engine.evaluate_and_apply_sync(scenario_id, tool_name, tool_args)
    if not fault.triggered:
        return None

    if fault.error:
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": fault.error.get("message", "Chaos Fault Injected")}],
                "isError": True,
                "_meta": {"fault_type": fault.fault_type.value if fault.fault_type else "chaos"},
            },
        })
    elif fault.malformed_response:
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": fault.malformed_response,
        })
    return None
