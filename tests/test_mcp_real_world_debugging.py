"""
Comprehensive Test Suite for Real-World Debugging & Reproduction Engine:
- Chaos & Fault Injection Engine (Latency, Errors, HTTP status, Malformed payloads)
- Session Execution Trace Recording
- Time-Travel State Snapshots (Create, Restore, Diff)
- Self-Contained Repro Bundle Export (.mcp-repro.json)
- Deterministic Trace Replay & Result Drift Analysis
- REST API Debug Endpoints
"""

import pytest
import json
from pathlib import Path
from starlette.testclient import TestClient

from mock_mcp_gateway.chaos import FaultRule, FaultType, chaos_engine
from mock_mcp_gateway.manager import ServerManager
from mock_mcp_gateway.recorder import ReplayEngine, ReproBundle, trace_recorder
from mock_mcp_gateway.schemas import schema_registry
from mock_mcp_gateway.session import current_session_id, session_manager
from mock_mcp_gateway.tools import (
    clear_fault_rules,
    create_session_snapshot,
    diff_session_snapshots,
    export_repro_bundle,
    get_execution_trace,
    inject_fault_rule,
    manage_users,
    replay_session_trace,
    restore_session_snapshot,
)


@pytest.fixture(autouse=True)
def setup_test_env():
    """Ensure clean chaos rules, traces, and loaded schemas before each test."""
    chaos_engine.clear_rules()
    schema_registry.load_schemas(
        schemas_dir=Path("config/schemas/tools"),
        catalog_file=Path("config/schemas/tools.json"),
    )


def test_time_travel_state_snapshots():
    """Test creating, restoring, and diffing session state snapshots."""
    session_id = "debug_session_1"
    token = current_session_id.set(session_id)

    try:
        # 1. Initialize session with data
        session = session_manager.get_or_create_session(session_id=session_id)
        session.user_db["user_test"] = {"id": "user_test", "name": "Initial User"}

        # 2. Create snapshot 1
        snap1_res = create_session_snapshot(label="Checkpoint 1")
        snap1_id = snap1_res["snapshot_id"]
        assert snap1_res["status"] == "created"

        # 3. Mutate session state
        session.user_db["user_test"]["name"] = "Modified User Name"
        session.user_db["user_test2"] = {"id": "user_test2", "name": "New User"}

        # 4. Create snapshot 2
        snap2_res = create_session_snapshot(label="Checkpoint 2")
        snap2_id = snap2_res["snapshot_id"]

        # 5. Diff snapshots
        diff_res = diff_session_snapshots(snap1_id, snap2_id)
        assert len(diff_res["diff"]) > 0

        # 6. Restore snapshot 1
        restore_res = restore_session_snapshot(snap1_id)
        assert restore_res["status"] == "restored"
        assert session.user_db["user_test"]["name"] == "Initial User"
        assert "user_test2" not in session.user_db
    finally:
        current_session_id.reset(token)


def test_chaos_fault_injection_engine():
    """Test real-world chaos fault injection rules (latency, error, malformed)."""
    # 1. Inject error fault rule for manage_users
    inj_res = inject_fault_rule(
        scenario_id="*",
        tool_name="manage_users",
        fault_type="error_rate",
        rate=1.0,
        error_message="Backend Database Connection Failure",
    )
    assert inj_res["status"] == "active"

    # 2. Call manage_users and verify injected error
    res = manage_users(action="list", session_id="chaos_sess")
    assert getattr(res, "is_error", False) is True
    assert "Backend Database Connection Failure" in str(res.content)

    # 3. Clear chaos rules
    clear_res = clear_fault_rules()
    assert clear_res["cleared_count"] >= 1

    # 4. Verify clean execution restored
    res_clean = manage_users(action="list", session_id="chaos_sess")
    assert isinstance(res_clean, dict)
    assert res_clean["count"] >= 0


def test_session_execution_trace_recording():
    """Test trace recording of tool calls and state transitions."""
    trace_recorder.clear_session_trace("trace_sess")

    # Manually record a call step
    rec = trace_recorder.record_call(
        session_id="trace_sess",
        scenario_id="sysops",
        tool_name="manage_users",
        tool_args={"action": "list"},
        initial_state={"v": 1},
        final_state={"v": 2},
        duration_ms=12.5,
        is_error=False,
        result_payload={"count": 2},
    )

    assert rec.step_index == 1
    assert rec.session_id == "trace_sess"
    assert rec.initial_state_hash != rec.final_state_hash

    # Retrieve execution trace via meta-tool
    trace_res = get_execution_trace(session_id="trace_sess")
    assert trace_res["count"] == 1
    assert trace_res["trace"][0]["tool_name"] == "manage_users"


def test_export_and_replay_repro_bundle():
    """Test exporting self-contained ReproBundle (.mcp-repro.json) and replaying trace."""
    session_id = "repro_sess"
    trace_recorder.clear_session_trace(session_id)

    # Record two execution steps
    trace_recorder.record_call(
        session_id=session_id,
        scenario_id="sysops",
        tool_name="echo",
        tool_args={"message": "hello"},
        initial_state={},
        final_state={},
        duration_ms=5.0,
        result_payload={"reversed": "olleh"},
    )

    # Export bundle
    bundle_dict = export_repro_bundle(session_id=session_id)
    assert bundle_dict["session_id"] == session_id
    assert len(bundle_dict["trace_records"]) == 1

    # Replay bundle via meta-tool
    bundle_json = json.dumps(bundle_dict)
    replay_res = replay_session_trace(repro_bundle_json=bundle_json)
    assert isinstance(replay_res, dict)
    assert replay_res["total_steps"] == 1
    assert replay_res["drift_detected"] is False


def test_debug_rest_api_endpoints():
    """Test REST API debug endpoints for trace, repro export, chaos, and snapshots."""
    manager = ServerManager("config/mcp_servers.yaml")
    app = manager.create_gateway_app()
    client = TestClient(app)

    session_id = "api_debug_sess"

    # 1. POST /sessions/{session_id}/snapshots
    resp = client.post(f"/sessions/{session_id}/snapshots", json={"label": "API Checkpoint"})
    assert resp.status_code == 201
    snap_data = resp.json()
    assert snap_data["status"] == "created"
    snap_id = snap_data["snapshot_id"]

    # 2. POST /sessions/{session_id}/snapshots/restore
    resp = client.post(f"/sessions/{session_id}/snapshots/restore", json={"snapshot_id": snap_id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "restored"

    # 3. POST /scenarios/sysops/chaos
    resp = client.post(
        "/scenarios/sysops/chaos",
        json={"tool_name": "echo", "fault_type": "latency", "delay_ms": 10},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    # 4. DELETE /scenarios/sysops/chaos
    resp = client.delete("/scenarios/sysops/chaos")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cleared"

    # 5. GET /sessions/{session_id}/trace
    resp = client.get(f"/sessions/{session_id}/trace")
    assert resp.status_code == 200
    assert "trace" in resp.json()

    # 6. GET /sessions/{session_id}/repro
    resp = client.get(f"/sessions/{session_id}/repro")
    assert resp.status_code == 200
    assert "trace_records" in resp.json()
