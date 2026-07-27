"""
Main entry point for starting Enterprise Mock MCP Gateway Server(s).
Supports Enterprise Gateway, Multi-Port, Hybrid modes, and Loguru logging.
"""

import argparse
import os
from pathlib import Path
import sys

from mcp_gateway.logger import logger, setup_logging
from mcp_gateway.manager import ServerManager
from mcp_gateway.server import mcp


def main():
    setup_logging()

    parser = argparse.ArgumentParser(
        description="Run Enterprise Mock MCP Gateway Server(s)."
    )

    parser.add_argument(
        "--config",
        type=str,
        default=os.getenv("MCP_CONFIG_PATH", "config/mcp_servers.yaml"),
        help="Path to YAML configuration file for multi-server setup (default: config/mcp_servers.yaml)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default=os.getenv("MCP_MODE", "gateway"),
        choices=["gateway", "single"],
        help="Server run mode: 'gateway' (shared port 2026 via unified gateway & adapters) or 'single'",
    )
    parser.add_argument(
        "--transport",
        type=str,
        default=os.getenv("FASTMCP_TRANSPORT", "streamable-http"),
        choices=["streamable-http"],
        help="Streamable HTTP transport protocol for single mode (default: streamable-http)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=os.getenv("FASTMCP_HOST", "0.0.0.0"),
        help="Host address to bind to in single mode (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("FASTMCP_PORT", "2026")),
        help="Port number to bind to in single mode (default: 2026)",
    )

    args = parser.parse_args()

    config_file = Path(args.config)

    # If mode is gateway and config exists, use ServerManager
    if args.mode == "gateway" and config_file.exists():
        logger.info(f"Loading Enterprise Multi-MCP configuration from: {config_file}")
        manager = ServerManager(config_file)
        manager.run_gateway()
    else:
        # Fallback to single server mode (Streamable HTTP)
        logger.info(f"Starting single Mock MCP Server using transport '{args.transport}'...")
        mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
