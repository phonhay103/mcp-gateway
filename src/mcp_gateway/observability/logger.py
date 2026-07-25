"""
Centralized Enterprise Logging Configuration using Loguru.
Provides structured JSON logging, console formatting, SIEM audit logging,
and Cryptographically Signed Audit Records (HMAC-SHA256).
"""

import hashlib
import hmac
from pathlib import Path
import sys
import time
from typing import Any, Dict, Optional
from loguru import logger

DEFAULT_AUDIT_HMAC_SECRET = "enterprise-mcp-audit-secret-key-2026"


def setup_logging(
    log_level: str = "INFO",
    json_format: bool = False,
    log_file: Optional[Path] = None,
) -> None:
    """
    Configure global Loguru logger for console and optional file/JSON output.
    """
    logger.remove()

    if json_format:
        logger.add(
            sys.stderr,
            level=log_level,
            serialize=True,
            backtrace=True,
            diagnose=True,
        )
    else:
        logger.add(
            sys.stderr,
            level=log_level,
            format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            backtrace=True,
            diagnose=True,
        )

    if log_file:
        logger.add(
            log_file,
            level=log_level,
            rotation="10 MB",
            retention="30 days",
            serialize=json_format,
        )


def create_signed_audit_record(
    event_type: str,
    session_id: str,
    client_name: str,
    tool_name: str,
    status_code: int,
    payload_summary: Optional[Dict[str, Any]] = None,
    secret_key: str = DEFAULT_AUDIT_HMAC_SECRET,
) -> Dict[str, Any]:
    """
    Generates an immutable, cryptographically signed audit record (HMAC-SHA256)
    for regulatory compliance and SIEM tamper-proof auditing.
    """
    import json

    timestamp = int(time.time())
    record_body = {
        "timestamp": timestamp,
        "event_type": event_type,
        "session_id": session_id,
        "client_name": client_name,
        "tool_name": tool_name,
        "status_code": status_code,
        "payload_summary": payload_summary or {},
    }

    canonical_bytes = json.dumps(record_body, sort_keys=True, ensure_ascii=True).encode("utf-8")
    signature = hmac.new(
        secret_key.encode("utf-8"),
        canonical_bytes,
        hashlib.sha256,
    ).hexdigest()

    record_body["signature"] = f"sha256={signature}"
    return record_body


def log_audit_event(
    event_type: str,
    request,
    status_code: int,
    client_name: str = "anonymous",
    message: str = "",
    tool_name: str = "N/A",
) -> None:
    """
    Emit a structured audit event log via Loguru with cryptographic HMAC signature.
    """
    request_id = getattr(getattr(request, "state", None), "request_id", "N/A")
    session_id = getattr(getattr(request, "state", None), "session_id", "N/A")
    client_ip = request.client.host if getattr(request, "client", None) else "unknown"

    signed_record = create_signed_audit_record(
        event_type=event_type,
        session_id=session_id,
        client_name=client_name,
        tool_name=tool_name,
        status_code=status_code,
        payload_summary={"path": request.url.path, "method": request.method, "message": message},
    )

    audit_logger = logger.bind(
        audit=True,
        event_type=event_type,
        request_id=request_id,
        session_id=session_id,
        client_ip=client_ip,
        client_name=client_name,
        method=request.method,
        path=request.url.path,
        status_code=status_code,
        signature=signed_record["signature"],
    )

    if status_code >= 400:
        audit_logger.warning(
            f"AUDIT [{event_type}] {request.method} {request.url.path} ({status_code}) - {message} [sig:{signed_record['signature'][:12]}...]"
        )
    else:
        audit_logger.info(
            f"AUDIT [{event_type}] {request.method} {request.url.path} ({status_code}) - {message} [sig:{signed_record['signature'][:12]}...]"
        )


__all__ = ["logger", "setup_logging", "log_audit_event", "create_signed_audit_record"]
