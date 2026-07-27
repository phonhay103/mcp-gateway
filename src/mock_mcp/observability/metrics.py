"""
Prometheus and OpenTelemetry compatible metrics collector for Enterprise MCP Gateway.
Exposes standard metrics endpoint (/metrics).
"""

import time
from typing import Dict


class MetricsCollector:
    """
    In-memory OpenTelemetry/Prometheus compatible metrics collector.
    """

    def __init__(self):
        self._requests_total: Dict[str, int] = {}
        self._tool_calls_total: Dict[str, int] = {}
        self._audit_events_total: Dict[str, int] = {}
        self._start_time: float = time.time()

    def record_request(self, method: str, path: str, status_code: int) -> None:
        key = f'{method}:{path}:{status_code}'
        self._requests_total[key] = self._requests_total.get(key, 0) + 1

    def record_tool_call(self, tool_name: str, status: str = "success") -> None:
        key = f'{tool_name}:{status}'
        self._tool_calls_total[key] = self._tool_calls_total.get(key, 0) + 1

    def record_audit_event(self, event_type: str) -> None:
        self._audit_events_total[event_type] = self._audit_events_total.get(event_type, 0) + 1

    def generate_prometheus_format(self, active_sessions_count: int = 0) -> str:
        """Render metrics in Prometheus plain text exposition format."""
        lines = [
            "# HELP mock_mcp_uptime_seconds Total process uptime in seconds",
            "# TYPE mock_mcp_uptime_seconds gauge",
            f"mock_mcp_uptime_seconds {round(time.time() - self._start_time, 2)}",
            "",
            "# HELP mock_mcp_active_sessions Active session count",
            "# TYPE mock_mcp_active_sessions gauge",
            f"mock_mcp_active_sessions {active_sessions_count}",
            "",
            "# HELP mock_mcp_requests_total Total HTTP requests handled",
            "# TYPE mock_mcp_requests_total counter",
        ]

        for key, count in self._requests_total.items():
            parts = key.split(":")
            method, path, status = parts[0], parts[1], parts[2]
            lines.append(f'mock_mcp_requests_total{{method="{method}",path="{path}",status="{status}"}} {count}')

        lines.extend([
            "",
            "# HELP mock_mcp_tool_calls_total Total MCP tool executions",
            "# TYPE mock_mcp_tool_calls_total counter",
        ])

        for key, count in self._tool_calls_total.items():
            parts = key.split(":")
            tool_name, status = parts[0], parts[1]
            lines.append(f'mock_mcp_tool_calls_total{{tool_name="{tool_name}",status="{status}"}} {count}')

        lines.extend([
            "",
            "# HELP mock_mcp_audit_events_total Total security audit events",
            "# TYPE mock_mcp_audit_events_total counter",
        ])

        for event_type, count in self._audit_events_total.items():
            lines.append(f'mock_mcp_audit_events_total{{event_type="{event_type}"}} {count}')

        return "\n".join(lines) + "\n"


metrics_collector = MetricsCollector()
