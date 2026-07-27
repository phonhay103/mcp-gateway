"""
Logger module for mock_mcp service.
"""

import sys
from loguru import logger

def setup_logging(level: str = "INFO"):
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level:10}</level> | <cyan>{name}:{line}</cyan> - <level>{message}</level>",
        level=level,
    )

__all__ = ["logger", "setup_logging"]
