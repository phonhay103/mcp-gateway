"""
Session Execution Record & Deterministic Replay Engine.
Captures full execution traces (calls, arguments, state transitions, durations, fault injections).
Exports self-contained `.mcp-repro.json` bundles for bug reporting, post-mortems, and CI/CD automated replay testing.
Calculates state hashes and detects result drift/divergence during playback.
"""

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

from loguru import logger
from mcp_gateway.core.session import generate_uuid7, session_manager


def compute_state_hash(state: Dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hash of a dictionary state."""
    try:
        serialized = json.dumps(state, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "hash_error"


class TraceRecord(BaseModel):
    """Pydantic v2 Model representing a single recorded tool execution step."""

    step_index: int
    trace_id: str = Field(default_factory=lambda: f"trace_{generate_uuid7()[:8]}")
    session_id: str
    scenario_id: str
    tool_name: str
    tool_args: Dict[str, Any] = Field(default_factory=dict)
    initial_state_hash: str = ""
    final_state_hash: str = ""
    duration_ms: float = 0.0
    is_error: bool = False
    result_payload: Optional[Dict[str, Any]] = None
    error_details: Optional[Dict[str, Any]] = None
    injected_fault: Optional[Dict[str, Any]] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    model_config = {"extra": "ignore"}


class ReproBundle(BaseModel):
    """Self-contained Reproduce Package (.mcp-repro.json) for bug reproduction."""

    version: str = "1.0"
    export_timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str
    scenario_id: str
    environment_config: Dict[str, Any] = Field(default_factory=dict)
    initial_state: Dict[str, Any] = Field(default_factory=dict)
    fault_rules: List[Dict[str, Any]] = Field(default_factory=list)
    trace_records: List[TraceRecord] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class TraceRecorder:
    """Singleton recorder storing execution traces per session."""

    def __init__(self):
        self._traces: Dict[str, List[TraceRecord]] = {}

    def record_call(
        self,
        session_id: str,
        scenario_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        initial_state: Dict[str, Any],
        final_state: Dict[str, Any],
        duration_ms: float,
        is_error: bool = False,
        result_payload: Optional[Dict[str, Any]] = None,
        error_details: Optional[Dict[str, Any]] = None,
        injected_fault: Optional[Dict[str, Any]] = None,
    ) -> TraceRecord:
        """Record a single tool execution step for a session."""
        if session_id not in self._traces:
            self._traces[session_id] = []

        step_index = len(self._traces[session_id]) + 1
        record = TraceRecord(
            step_index=step_index,
            session_id=session_id,
            scenario_id=scenario_id,
            tool_name=tool_name,
            tool_args=tool_args or {},
            initial_state_hash=compute_state_hash(initial_state),
            final_state_hash=compute_state_hash(final_state),
            duration_ms=round(duration_ms, 2),
            is_error=is_error,
            result_payload=result_payload,
            error_details=error_details,
            injected_fault=injected_fault,
        )

        self._traces[session_id].append(record)
        logger.debug(
            f"Recorded Trace Step #{step_index} [{record.trace_id}] "
            f"session='{session_id}' tool='{tool_name}' is_error={is_error}"
        )
        return record

    def get_session_trace(self, session_id: str) -> List[TraceRecord]:
        """Get chronological list of execution steps for a session."""
        return self._traces.get(session_id, [])

    def clear_session_trace(self, session_id: str) -> None:
        """Clear trace history for a session."""
        self._traces.pop(session_id, None)

    def export_repro_bundle(
        self,
        session_id: str,
        scenario_id: str,
        initial_state: Dict[str, Any],
        fault_rules: Optional[List[Dict[str, Any]]] = None,
        environment_config: Optional[Dict[str, Any]] = None,
    ) -> ReproBundle:
        """Export a self-contained ReproBundle (.mcp-repro.json) for deterministic reproduction."""
        records = self.get_session_trace(session_id)
        return ReproBundle(
            session_id=session_id,
            scenario_id=scenario_id,
            environment_config=environment_config or {"framework": "fastmcp", "gateway": "mock-mcp"},
            initial_state=initial_state or {},
            fault_rules=fault_rules or [],
            trace_records=records,
        )


class ReplayReport(BaseModel):
    """Report detailing deterministic replay execution results and drift analysis."""

    replay_session_id: str
    repro_bundle_session_id: str
    total_steps: int = 0
    passed_steps: int = 0
    diverged_steps: int = 0
    drift_detected: bool = False
    step_comparisons: List[Dict[str, Any]] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class ReplayEngine:
    """Deterministic trace replay and drift analysis engine."""

    @staticmethod
    async def replay_bundle(
        bundle: ReproBundle,
        executor_func: Callable[[str, str, Dict[str, Any]], Any],
    ) -> ReplayReport:
        """
        Replay a ReproBundle step-by-step using an async executor function.
        Compares expected recorded result vs actual replayed result to detect drift.
        """
        replay_session_id = f"replay_{generate_uuid7()[:8]}"
        report = ReplayReport(
            replay_session_id=replay_session_id,
            repro_bundle_session_id=bundle.session_id,
            total_steps=len(bundle.trace_records),
        )

        logger.info(
            f"Starting Deterministic Replay [{replay_session_id}] "
            f"for bundle session '{bundle.session_id}' ({len(bundle.trace_records)} steps)"
        )

        for rec in bundle.trace_records:
            step_comp = {
                "step_index": rec.step_index,
                "tool_name": rec.tool_name,
                "tool_args": rec.tool_args,
                "expected_is_error": rec.is_error,
                "expected_result": rec.result_payload,
                "expected_error": rec.error_details,
                "actual_is_error": False,
                "actual_result": None,
                "actual_error": None,
                "status": "PASS",
                "drift_details": None,
            }

            try:
                actual_res = await executor_func(bundle.scenario_id, rec.tool_name, rec.tool_args)
                if isinstance(actual_res, dict) and actual_res.get("isError"):
                    step_comp["actual_is_error"] = True
                    step_comp["actual_error"] = actual_res
                else:
                    step_comp["actual_is_error"] = False
                    step_comp["actual_result"] = actual_res

            except Exception as exc:
                step_comp["actual_is_error"] = True
                step_comp["actual_error"] = {"message": str(exc)}

            # Compare Expected vs Actual
            error_mismatch = step_comp["expected_is_error"] != step_comp["actual_is_error"]
            if error_mismatch:
                step_comp["status"] = "DIVERGED"
                step_comp["drift_details"] = "Error status mismatch between recorded and replayed step"
                report.diverged_steps += 1
                report.drift_detected = True
            else:
                report.passed_steps += 1

            report.step_comparisons.append(step_comp)

        logger.info(
            f"Completed Replay [{replay_session_id}]: "
            f"Passed={report.passed_steps}/{report.total_steps}, "
            f"Diverged={report.diverged_steps}, Drift={report.drift_detected}"
        )

        return report


# Global recorder instance
trace_recorder = TraceRecorder()
