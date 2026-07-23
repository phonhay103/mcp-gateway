"""
Centralized Enterprise Logging Configuration using Loguru.
Provides structured JSON logging, console formatting, and SIEM audit logging.
"""

from pathlib import Path
import sys
from typing import Optional
from loguru import logger


def setup_logging(
    log_level: str = "INFO",
    log_file: Optional[str] = "logs/mcp_gateway.log",
    json_format: bool = False,
) -> None:
    """
    Configure Loguru logger sinks for console and optional file logging.
    """
    # Remove default handler
    logger.remove()

    # 1. Console Sink
    if json_format:
        logger.add(
            sys.stdout,
            level=log_level,
            serialize=True,
        )
    else:
        logger.add(
            sys.stdout,
            level=log_level,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
                "<level>{message}</level>"
            ),
            colorize=True,
        )

    # 2. File Sink (with rotation and retention)
    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            file_path,
            level=log_level,
            rotation="10 MB",
            retention="7 days",
            compression="zip",
            serialize=True,  # Always write structured JSON to log file for SIEM
            encoding="utf-8",
        )


def log_audit_event(
    event_type: str,
    request,
    status_code: int,
    client_name: str = "anonymous",
    message: str = "",
) -> None:
    """
    Emit a structured audit event log via Loguru.
    """
    request_id = getattr(getattr(request, "state", None), "request_id", "N/A")
    client_ip = request.client.host if getattr(request, "client", None) else "unknown"

    audit_logger = logger.bind(
        audit=True,
        event_type=event_type,
        request_id=request_id,
        client_ip=client_ip,
        client_name=client_name,
        method=request.method,
        path=request.url.path,
        status_code=status_code,
    )

    if status_code >= 400:
        audit_logger.warning(f"AUDIT [{event_type}] {request.method} {request.url.path} ({status_code}) - {message}")
    else:
        audit_logger.info(f"AUDIT [{event_type}] {request.method} {request.url.path} ({status_code}) - {message}")


# Export pre-configured logger instance
__all__ = ["logger", "setup_logging", "log_audit_event"]
