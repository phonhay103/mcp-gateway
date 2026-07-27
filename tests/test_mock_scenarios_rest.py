"""
Unit and integration tests verifying Mock Service scenario REST API endpoints.
"""

import pytest
from starlette.testclient import TestClient
from mock_mcp.core.manager import ServerManager


def test_mock_scenarios_rest_api():
    """Verify scenarios listing, activation, state inspection, and manipulation via HTTP REST."""
    manager = ServerManager("configs/mock_mcp/mock_config.yaml")
    app = manager.create_mock_app()

    with TestClient(app) as client:
        # 1. GET /scenarios - List scenarios
        res = client.get("/scenarios")
        assert res.status_code == 200
        scenarios = res.json()
        assert len(scenarios) >= 3
        
        # Check structure
        ids = [sc["id"] for sc in scenarios]
        assert "full-scenario" in ids
        assert "sysops-scenario" in ids
        assert "utility-scenario" in ids

        # The alphabetically first scenario is chaos-scenario, so full-scenario is not the default at startup
        full_sc = next(sc for sc in scenarios if sc["id"] == "full-scenario")
        assert full_sc["is_default"] is False
        assert "get_system_info" in full_sc["tools"]

        # 2. GET /scenarios/{scenario_id} - Get scenario details
        res_single = client.get("/scenarios/sysops-scenario")
        assert res_single.status_code == 200
        assert res_single.json()["name"] == "SysOps Monitoring Scenario"

        # Test 404
        res_404 = client.get("/scenarios/nonexistent-scenario")
        assert res_404.status_code == 404

        # 3. POST /scenarios/{scenario_id}/activate - Activate scenario globally
        res_act = client.post("/scenarios/utility-scenario/activate")
        assert res_act.status_code == 200
        assert res_act.json()["active_default_scenario_id"] == "utility-scenario"

        # Verify lists show utility-scenario as default
        res_list_new = client.get("/scenarios")
        utility_sc = next(sc for sc in res_list_new.json() if sc["id"] == "utility-scenario")
        assert utility_sc["is_default"] is True

        # 4. GET /scenarios/{scenario_id}/state - Inspect in-memory state
        res_state = client.get("/scenarios/sysops-scenario/state")
        assert res_state.status_code == 200
        state_data = res_state.json()
        assert "metrics" in state_data or "_tool_behaviors" in state_data

        # 5. POST /scenarios/{scenario_id}/state - Update in-memory state
        res_update = client.post("/scenarios/sysops-scenario/state", json={"custom_flag": "hello_world"})
        assert res_update.status_code == 200
        assert res_update.json()["state"]["custom_flag"] == "hello_world"

        # Verify update persisted
        res_state_updated = client.get("/scenarios/sysops-scenario/state")
        assert res_state_updated.json()["custom_flag"] == "hello_world"

        # 6. POST /scenarios/{scenario_id}/reset - Reset scenario back to defaults
        res_reset = client.post("/scenarios/sysops-scenario/reset")
        assert res_reset.status_code == 200
        assert "custom_flag" not in res_reset.json()["state"]
