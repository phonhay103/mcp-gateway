"""
Observability Package (Logger, Metrics, and Recorder).
"""

from mcp_gateway.observability.logger import (
    DEFAULT_AUDIT_HMAC_SECRET,
    create_signed_audit_record,
    log_audit_event,
    logger,
    setup_logging,
)
from mcp_gateway.observability.metrics import metrics_collector
from mcp_gateway.observability.recorder import trace_recorder

__all__ = [
    "logger",
    "setup_logging",
    "log_audit_event",
    "create_signed_audit_record",
    "metrics_collector",
    "trace_recorder",
    "DEFAULT_AUDIT_HMAC_SECRET",
]
