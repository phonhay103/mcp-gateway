"""
Main entry point for starting the Enterprise Mock MCP Gateway Server.
Supports unified gateway routing and Loguru logging. All downstream servers must route through this gateway.
"""

import argparse
import os
from pathlib import Path
import sys

from mcp_gateway.logger import logger, setup_logging
from mcp_gateway.manager import ServerManager


def main():
    setup_logging()

    parser = argparse.ArgumentParser(
        description="Run Enterprise Mock MCP Gateway Server."
    )

    parser.add_argument(
        "--config",
        type=str,
        default=os.getenv("MCP_CONFIG_PATH", "config/mcp_servers.yaml"),
        help="Path to YAML configuration file for gateway setup (default: config/mcp_servers.yaml)",
    )

    args = parser.parse_args()

    config_file = Path(args.config)

    if not config_file.exists():
        logger.error(f"Configuration file not found: {config_file}")
        sys.exit(1)

    logger.info(f"Loading Enterprise MCP Gateway configuration from: {config_file}")
    manager = ServerManager(config_file)
    manager.run_gateway()


if __name__ == "__main__":
    main()
