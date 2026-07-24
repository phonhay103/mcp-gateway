"""
Chaos & Real-World Fault Injection Engine for MCP Gateway.
Simulates network latency, flaky failures, rate limits (HTTP 429), gateway timeouts (HTTP 504),
bad gateway errors (HTTP 502/503), schema drift, corrupted JSON, and timeout cancellations.
Allows deterministic or probabilistic bug reproduction during development, testing, and debugging.
"""

import asyncio
from enum import Enum
import fnmatch
import random
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from mock_mcp_gateway.logger import logger
from mock_mcp_gateway.metrics import metrics_collector
from mock_mcp_gateway.session import generate_uuid7


class FaultType(str, Enum):
    """Supported real-world fault injection types."""

    LATENCY = "latency"
    ERROR_RATE = "error_rate"
    HTTP_STATUS = "http_status"
    MALFORMED_JSON = "malformed_json"
    SCHEMA_DRIFT = "schema_drift"
    TIMEOUT = "timeout"


class FaultRule(BaseModel):
    """Pydantic v2 Model defining a Fault Injection Rule."""

    rule_id: str = Field(default_factory=lambda: f"rule_{generate_uuid7()[:8]}")
    scenario_id: str = Field(default="*", description="Target scenario ID or '*' for all")
    tool_name: str = Field(default="*", description="Target tool name or fnmatch pattern (e.g. 'manage_*')")
    fault_type: FaultType = Field(default=FaultType.LATENCY, description="Type of fault to inject")
    rate: float = Field(default=1.0, ge=0.0, le=1.0, description="Probability rate (0.0 to 1.0)")
    delay_ms: float = Field(default=0.0, ge=0.0, description="Latency delay in milliseconds")
    http_status: Optional[int] = Field(default=None, description="HTTP status code (e.g. 429, 502, 503, 504)")
    error_message: str = Field(default="Simulated chaos engineering fault", description="Error message text")
    error_code: Optional[int] = Field(default=-32000, description="JSON-RPC error code")
    malformed_payload: Optional[Dict[str, Any]] = Field(default=None, description="Corrupted JSON response structure")
    schema_drift_field: Optional[str] = Field(default=None, description="FieldName to mutate or drop for schema drift")

    model_config = {"extra": "ignore"}


class FaultActionResult(BaseModel):
    """Result container indicating applied chaos fault."""

    triggered: bool = False
    rule_id: Optional[str] = None
    fault_type: Optional[FaultType] = None
    delay_ms: float = 0.0
    error: Optional[Dict[str, Any]] = None
    http_status: Optional[int] = None
    malformed_response: Optional[Dict[str, Any]] = None
    timeout: bool = False

    model_config = {"extra": "ignore"}


class ChaosEngine:
    """Singleton engine managing dynamic fault injection rules for real-world bug reproduction."""

    def __init__(self):
        self._rules: Dict[str, FaultRule] = {}

    def add_rule(self, rule: FaultRule) -> str:
        """Register a new fault injection rule."""
        self._rules[rule.rule_id] = rule
        logger.info(
            f"Added fault rule [{rule.rule_id}] type={rule.fault_type.value} "
            f"scenario='{rule.scenario_id}' tool='{rule.tool_name}' rate={rule.rate}"
        )
        return rule.rule_id

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a fault rule by ID."""
        if rule_id in self._rules:
            self._rules.pop(rule_id)
            logger.info(f"Removed fault rule [{rule_id}]")
            return True
        return False

    def clear_rules(self, scenario_id: Optional[str] = None) -> int:
        """Clear all or scenario-scoped fault rules."""
        if not scenario_id or scenario_id == "*":
            count = len(self._rules)
            self._rules.clear()
            logger.info("Cleared all fault injection rules")
            return count

        to_remove = [
            rid for rid, r in self._rules.items() if r.scenario_id in (scenario_id, "*")
        ]
        for rid in to_remove:
            self._rules.pop(rid, None)
        logger.info(f"Cleared {len(to_remove)} fault rules for scenario '{scenario_id}'")
        return len(to_remove)

    def list_rules(self, scenario_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List active fault injection rules."""
        rules_list = []
        for r in self._rules.values():
            if not scenario_id or scenario_id == "*" or r.scenario_id in (scenario_id, "*"):
                rules_list.append(r.model_dump())
        return rules_list

    def _matches(self, rule: FaultRule, scenario_id: str, tool_name: str) -> bool:
        """Check if rule matches given scenario and tool name."""
        sc_match = rule.scenario_id == "*" or rule.scenario_id == scenario_id
        tool_match = rule.tool_name == "*" or fnmatch.fnmatch(tool_name, rule.tool_name)
        return sc_match and tool_match

    async def evaluate_and_apply(
        self, scenario_id: str, tool_name: str, tool_args: Optional[Dict[str, Any]] = None
    ) -> FaultActionResult:
        """Evaluate matching rules, roll probability, and apply fault injection asynchronously."""
        result = FaultActionResult()

        matching_rules = [
            r for r in self._rules.values() if self._matches(r, scenario_id, tool_name)
        ]

        if not matching_rules:
            return result

        for rule in matching_rules:
            if random.random() > rule.rate:
                continue

            result.triggered = True
            result.rule_id = rule.rule_id
            result.fault_type = rule.fault_type

            if rule.delay_ms > 0:
                result.delay_ms = rule.delay_ms
                logger.warning(f"Injecting simulated latency {rule.delay_ms}ms on tool '{tool_name}'")
                await asyncio.sleep(rule.delay_ms / 1000.0)

            if rule.fault_type == FaultType.LATENCY:
                pass
            elif rule.fault_type in (FaultType.ERROR_RATE, FaultType.TIMEOUT):
                result.error = {
                    "isError": True,
                    "message": rule.error_message,
                    "code": rule.error_code,
                    "fault_type": rule.fault_type.value,
                }
                if rule.fault_type == FaultType.TIMEOUT:
                    result.timeout = True
            elif rule.fault_type == FaultType.HTTP_STATUS:
                result.http_status = rule.http_status or 500
                result.error = {
                    "isError": True,
                    "message": f"Simulated HTTP {result.http_status} Error: {rule.error_message}",
                    "code": -32000 - result.http_status,
                }
            elif rule.fault_type == FaultType.MALFORMED_JSON:
                result.malformed_response = rule.malformed_payload or {
                    "corrupted_field": "\x00\xff_INVALID_PAYLOAD_STRUCTURE_",
                    "status": "MALFORMED_SIMULATION",
                }
            elif rule.fault_type == FaultType.SCHEMA_DRIFT:
                field = rule.schema_drift_field or "unexpected_drift_param"
                result.malformed_response = {
                    field: "DRIFT_SIMULATED_VALUE",
                    "_schema_version_drift": "v999.0_UNEXPECTED",
                }

            metrics_collector.record_audit_event("CHAOS_FAULT_INJECTED")
            logger.warning(
                f"Chaos Fault Injected [{rule.rule_id}] type={rule.fault_type.value} "
                f"tool='{tool_name}' scenario='{scenario_id}'"
            )
            break

        return result

    def evaluate_and_apply_sync(
        self, scenario_id: str, tool_name: str, tool_args: Optional[Dict[str, Any]] = None
    ) -> FaultActionResult:
        """Synchronous version of evaluate_and_apply using time.sleep for threadpool tools."""
        result = FaultActionResult()

        matching_rules = [
            r for r in self._rules.values() if self._matches(r, scenario_id, tool_name)
        ]

        if not matching_rules:
            return result

        for rule in matching_rules:
            if random.random() > rule.rate:
                continue

            result.triggered = True
            result.rule_id = rule.rule_id
            result.fault_type = rule.fault_type

            if rule.delay_ms > 0:
                result.delay_ms = rule.delay_ms
                logger.warning(f"Injecting simulated latency {rule.delay_ms}ms on tool '{tool_name}'")
                time.sleep(rule.delay_ms / 1000.0)

            if rule.fault_type == FaultType.LATENCY:
                pass
            elif rule.fault_type in (FaultType.ERROR_RATE, FaultType.TIMEOUT):
                result.error = {
                    "isError": True,
                    "message": rule.error_message,
                    "code": rule.error_code,
                    "fault_type": rule.fault_type.value,
                }
                if rule.fault_type == FaultType.TIMEOUT:
                    result.timeout = True
            elif rule.fault_type == FaultType.HTTP_STATUS:
                result.http_status = rule.http_status or 500
                result.error = {
                    "isError": True,
                    "message": f"Simulated HTTP {result.http_status} Error: {rule.error_message}",
                    "code": -32000 - result.http_status,
                }
            elif rule.fault_type == FaultType.MALFORMED_JSON:
                result.malformed_response = rule.malformed_payload or {
                    "corrupted_field": "\x00\xff_INVALID_PAYLOAD_STRUCTURE_",
                    "status": "MALFORMED_SIMULATION",
                }
            elif rule.fault_type == FaultType.SCHEMA_DRIFT:
                field = rule.schema_drift_field or "unexpected_drift_param"
                result.malformed_response = {
                    field: "DRIFT_SIMULATED_VALUE",
                    "_schema_version_drift": "v999.0_UNEXPECTED",
                }

            metrics_collector.record_audit_event("CHAOS_FAULT_INJECTED")
            logger.warning(
                f"Chaos Fault Injected [{rule.rule_id}] type={rule.fault_type.value} "
                f"tool='{tool_name}' scenario='{scenario_id}'"
            )
            break

        return result


# Global singleton instance
chaos_engine = ChaosEngine()
