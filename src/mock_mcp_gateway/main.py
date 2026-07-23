"""
Main entry point for starting Enterprise Mock MCP Gateway Server(s).
Supports Enterprise Gateway, Multi-Port, Hybrid modes, and Loguru logging.
"""

import argparse
import os
from pathlib import Path
import sys

from mock_mcp_gateway.logger import logger, setup_logging
from mock_mcp_gateway.manager import ServerManager
from mock_mcp_gateway.server import mcp


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
        default=os.getenv("MCP_MODE", "hybrid"),
        choices=["gateway", "multi-port", "hybrid", "single"],
        help="Server run mode: 'gateway' (shared port 8000), 'multi-port' (dedicated ports 8001..), 'hybrid' (both), or 'single'",
    )
    parser.add_argument(
        "--transport",
        type=str,
        default=os.getenv("FASTMCP_TRANSPORT", "sse"),
        choices=["sse", "streamable-http", "http"],
        help="HTTP Streamable transport protocol for single mode (default: sse)",
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
        default=int(os.getenv("FASTMCP_PORT", "8000")),
        help="Port number to bind to in single mode (default: 8000)",
    )

    args = parser.parse_args()

    config_file = Path(args.config)

    # If mode is gateway, multi-port, or hybrid and config exists, use ServerManager
    if args.mode in ("gateway", "multi-port", "hybrid") and config_file.exists():
        logger.info(f"Loading Enterprise Multi-MCP configuration from: {config_file}")
        manager = ServerManager(config_file)

        if args.mode == "gateway":
            manager.run_gateway()
        elif args.mode == "multi-port":
            manager.run_multi_port()
        elif args.mode == "hybrid":
            manager.run_hybrid()
    else:
        # Fallback to single server mode (HTTP Streamable / SSE)
        logger.info(f"Starting single Mock MCP Server using transport '{args.transport}'...")
        mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
