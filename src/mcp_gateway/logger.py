"""
Facade re-export for logger module.
"""

from mcp_gateway.observability.logger import (
    DEFAULT_AUDIT_HMAC_SECRET,
    create_signed_audit_record,
    log_audit_event,
    logger,
    setup_logging,
)

__all__ = [
    "logger",
    "setup_logging",
    "log_audit_event",
    "create_signed_audit_record",
    "DEFAULT_AUDIT_HMAC_SECRET",
]
