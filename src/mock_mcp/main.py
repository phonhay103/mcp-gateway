"""
Mock MCP Service Entrypoint.
A standalone service providing isolated scenarios, fixtures, and chaos testing for MCP clients.
"""

import argparse
import os
from pathlib import Path
import sys
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from mock_mcp.core.manager import ServerManager

def main():
    parser = argparse.ArgumentParser(description="Run Mock MCP Service (Scenarios & Fixtures).")
    parser.add_argument(
        "--config",
        type=str,
        default=os.getenv("MCP_CONFIG_PATH", "config/mcp_servers.yaml"),
        help="Path to YAML configuration file (default: config/mcp_servers.yaml)",
    )

    args = parser.parse_args()
    config_file = Path(args.config)

    if not config_file.exists():
        print(f"Error: Configuration file not found: {config_file}")
        sys.exit(1)

    print(f"Starting Mock MCP Service using config: {config_file}")
    manager = ServerManager(config_file)
    manager.run_gateway(port=2027)

if __name__ == "__main__":
    main()
