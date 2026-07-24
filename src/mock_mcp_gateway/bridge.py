"""
Protocol Bridging Module for Enterprise Mock MCP Gateway.
Converts local STDIO JSON-RPC process streams to cloud-native Streamable HTTP endpoints.
"""

import asyncio
import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional
from mock_mcp_gateway.logger import logger


class STDIOBridgeAdapter:
    """
    Adapter launching an STDIO MCP Server process and bridging JSON-RPC messages
    between HTTP requests and stdin/stdout pipes.
    """

    def __init__(self, command: list[str], cwd: Optional[Path | str] = None):
        self.command = command
        self.cwd = str(cwd) if cwd else None
        self.process: Optional[asyncio.subprocess.Process] = None

    async def start(self) -> None:
        """Start the STDIO subprocess."""
        logger.info(f"[STDIO Bridge] Launching process: {' '.join(self.command)}")
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
        )

    async def send_jsonrpc_request(self, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send a JSON-RPC request dictionary over stdin and await JSON-RPC response from stdout.
        """
        if not self.process or self.process.returncode is not None:
            await self.start()

        if not self.process or not self.process.stdin or not self.process.stdout:
            raise RuntimeError("STDIO process failed to initialize pipes.")

        # Serialize request to line-delimited JSON-RPC
        payload_bytes = json.dumps(request_payload).encode("utf-8") + b"\n"
        self.process.stdin.write(payload_bytes)
        await self.process.stdin.drain()

        # Read response line from stdout
        response_line = await self.process.stdout.readline()
        if not response_line:
            raise RuntimeError("STDIO process closed stdout unexpectedly.")

        return json.loads(response_line.decode("utf-8"))

    async def stop(self) -> None:
        """Terminate the STDIO process."""
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
            logger.info("[STDIO Bridge] Process terminated.")
