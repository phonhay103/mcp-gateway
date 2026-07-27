"""
Real-World Debugging & Reproduction Meta-Tools (Trace, Chaos, Snapshots, Repro Export, Replay).
"""

import json
from typing import Annotated, Any, Dict, List, Optional
from fastmcp.tools.base import ToolResult
from pydantic import Field

from mock_mcp.core.session import session_manager


def get_execution_trace(
    session_id: Annotated[Optional[str], Field(default=None, description="Session ID to retrieve trace for (defaults to active session)")] = None
) -> Dict[str, Any]:
    """Real-World Debug Meta-Tool: Retrieve chronological execution trace of tool calls for a session."""
    from mock_mcp.recorder import trace_recorder

    session = session_manager.get_session(session_id=session_id)
    records = trace_recorder.get_session_trace(session.session_id)
    return {
        "session_id": session.session_id,
        "count": len(records),
        "trace": [r.model_dump() for r in records],
    }


def inject_fault_rule(
    scenario_id: Annotated[str, Field(default="*", description="Target scenario ID or '*' for all scenarios")] = "*",
    tool_name: Annotated[str, Field(default="*", description="Target tool name or fnmatch pattern (e.g. 'manage_*')")] = "*",
    fault_type: Annotated[str, Field(default="latency", description="Fault type ('latency', 'error_rate', 'http_status', 'malformed_json', 'schema_drift', 'timeout')")] = "latency",
    rate: Annotated[float, Field(default=1.0, ge=0.0, le=1.0, description="Probability rate (0.0 to 1.0)")] = 1.0,
    delay_ms: Annotated[float, Field(default=0.0, ge=0.0, description="Simulated latency delay in milliseconds")] = 0.0,
    error_message: Annotated[str, Field(default="Simulated chaos fault", description="Error message text")] = "Simulated chaos fault",
    http_status: Annotated[Optional[int], Field(default=None, description="HTTP status code (e.g. 429, 502, 503, 504)")] = None,
) -> Dict[str, Any]:
    """Real-World Debug Meta-Tool: Dynamically configure fault injection to reproduce flaky or edge-case bugs."""
    from mock_mcp.chaos import FaultRule, FaultType, chaos_engine

    try:
        ftype = FaultType(fault_type)
    except ValueError:
        ftype = FaultType.LATENCY

    rule = FaultRule(
        scenario_id=scenario_id,
        tool_name=tool_name,
        fault_type=ftype,
        rate=rate,
        delay_ms=delay_ms,
        error_message=error_message,
        http_status=http_status,
    )
    rule_id = chaos_engine.add_rule(rule)
    return {
        "status": "active",
        "rule_id": rule_id,
        "rule": rule.model_dump(),
    }


def clear_fault_rules(
    scenario_id: Annotated[Optional[str], Field(default=None, description="Target scenario ID to clear rules for, or None for all")] = None
) -> Dict[str, Any]:
    """Real-World Debug Meta-Tool: Clear active fault injection rules."""
    from mock_mcp.chaos import chaos_engine

    cleared = chaos_engine.clear_rules(scenario_id=scenario_id)
    return {
        "status": "cleared",
        "scenario_id": scenario_id or "all",
        "cleared_count": cleared,
    }


def create_session_snapshot(
    label: Annotated[str, Field(default="", description="Human readable label for the state snapshot")] = ""
) -> Dict[str, Any]:
    """Real-World Debug Meta-Tool: Take a time-travel snapshot checkpoint of current session state."""
    session = session_manager.get_session()
    snap_id = session.create_snapshot(label=label)
    return {
        "status": "created",
        "session_id": session.session_id,
        "snapshot_id": snap_id,
        "label": label or snap_id,
    }


def restore_session_snapshot(
    snapshot_id: Annotated[str, Field(description="Snapshot ID to restore session state to")]
) -> Dict[str, Any] | ToolResult:
    """Real-World Debug Meta-Tool: Time travel rollback session state to a previously saved snapshot."""
    session = session_manager.get_session()
    ok = session.restore_snapshot(snapshot_id)
    if not ok:
        return ToolResult(content=f"Snapshot '{snapshot_id}' not found for session '{session.session_id}'", is_error=True)

    return {
        "status": "restored",
        "session_id": session.session_id,
        "snapshot_id": snapshot_id,
        "current_state": session.state,
    }


def diff_session_snapshots(
    snapshot1_id: Annotated[str, Field(description="First snapshot ID")],
    snapshot2_id: Annotated[str, Field(description="Second snapshot ID")],
) -> Dict[str, Any]:
    """Real-World Debug Meta-Tool: Compare state difference between two snapshots."""
    session = session_manager.get_session()
    diff = session.diff_snapshots(snapshot1_id, snapshot2_id)
    return {
        "session_id": session.session_id,
        "snapshot1_id": snapshot1_id,
        "snapshot2_id": snapshot2_id,
        "diff": diff,
    }


def export_repro_bundle(
    session_id: Annotated[Optional[str], Field(default=None, description="Session ID to export repro bundle for")] = None
) -> Dict[str, Any]:
    """Real-World Debug Meta-Tool: Export a self-contained ReproBundle (.mcp-repro.json) for sharing & CI replay."""
    from mock_mcp.chaos import chaos_engine
    from mock_mcp.recorder import trace_recorder

    session = session_manager.get_session(session_id=session_id)
    faults = chaos_engine.list_rules(scenario_id=session.scenario_id)

    bundle = trace_recorder.export_repro_bundle(
        session_id=session.session_id,
        scenario_id=session.scenario_id,
        initial_state=session.state,
        fault_rules=faults,
    )
    return bundle.model_dump()


def replay_session_trace(
    repro_bundle_json: Annotated[str, Field(description="Serialized JSON string or object of a ReproBundle")]
) -> Dict[str, Any] | ToolResult:
    """Real-World Debug Meta-Tool: Deterministically replay a recorded trace bundle and return a drift analysis report."""
    from mock_mcp.recorder import ReplayEngine, ReproBundle

    try:
        if isinstance(repro_bundle_json, str):
            data = json.loads(repro_bundle_json)
        else:
            data = repro_bundle_json
        bundle = ReproBundle.model_validate(data)
    except Exception as exc:
        return ToolResult(content=f"Failed to parse ReproBundle: {exc}", is_error=True)

    async def _mock_executor(sc_id, tool_name, args):
        return {"status": "replayed", "tool": tool_name}

    import asyncio
    report = asyncio.run(ReplayEngine.replay_bundle(bundle, _mock_executor))
    return report.model_dump()
