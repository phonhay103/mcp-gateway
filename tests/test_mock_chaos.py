"""
Unit and integration tests verifying the declarative Chaos Fault Injection scenario.
"""

import time
import pytest
from mock_mcp.core.manager import ServerManager


def test_mock_chaos_fault_injection():
    """Verify that the chaos-scenario simulates declarative latency, error rates, and HTTP statuses at tool-level."""
    # Initialize the ServerManager to load and populate the scenario configs
    manager = ServerManager("configs/mock_mcp/mock_config.yaml")
    assert any(sc.id == "chaos-scenario" for sc in manager.scenario_configs)

    # Set the active scenario contextvar manually
    from mock_mcp.core.context import current_scenario_id
    token = current_scenario_id.set("chaos-scenario")
    
    try:
        from mock_mcp.tools.core_tools import check_scenario_tool_behavior
        
        start_time = time.time()
        res = check_scenario_tool_behavior("echo")
        duration_ms = (time.time() - start_time) * 1000
        
        # It must be an error ToolResult as http_status: 503 is set on the 'echo' tool behavior
        assert res is not None
        assert res.is_error is True
        assert "Simulated HTTP status error 503" in res.content[0].text
        assert res.meta == {"code": 503}
        
        # Check that latency (latency_ms: 100) was actually applied
        assert duration_ms >= 100
        
    finally:
        current_scenario_id.reset(token)
