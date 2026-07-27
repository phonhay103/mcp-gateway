"""
Auto-Registration Client for registering mock_mcp service with mcp_gateway.
"""

import asyncio
import os
from typing import List, Any, Optional
import httpx

from mock_mcp.logger import logger


async def register_scenarios_with_gateway(
    gateway_url: str,
    scenarios: List[Any],
    mock_service_url: str,
    api_key: str = "admin-secret-key-123",
    max_retries: int = 20,
    delay: float = 2.0,
) -> None:
    """
    Auto-register single unified mock_mcp server (or individual scenario endpoints) with mcp_gateway via POST /servers/register.
    Retries until mcp_gateway is ready, then registers the upstream server.
    """
    logger.info(f"Auto-registration task starting... Target Gateway: {gateway_url}")
    register_endpoint = f"{gateway_url.rstrip('/')}/servers/register"
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Step 1: Wait until gateway responds
        gateway_ready = False
        for attempt in range(1, max_retries + 1):
            try:
                resp = await client.get(gateway_url, headers=headers)
                if resp.status_code in (200, 401, 403, 404):
                    gateway_ready = True
                    logger.info(f"Gateway detected at {gateway_url} (HTTP {resp.status_code})")
                    break
            except Exception as exc:
                logger.debug(f"Waiting for mcp_gateway availability ({attempt}/{max_retries}): {exc}")
            await asyncio.sleep(delay)

        if not gateway_ready:
            logger.error(f"mcp_gateway at {gateway_url} not reachable after {max_retries} attempts.")
            return

        # Step 2: Register unified single mock server (/mcp)
        upstream_url = f"{mock_service_url.rstrip('/')}/mcp"
        payload = {
            "server_id": "mock-mcp-server",
            "name": "Unified Standalone Mock MCP Service",
            "upstream_url": upstream_url,
            "server_type": "mcp",
        }

        for reg_attempt in range(1, 5):
            try:
                resp = await client.post(register_endpoint, json=payload, headers=headers)
                if resp.status_code in (200, 201):
                    logger.info(f"Auto-registered unified mock_mcp server -> {upstream_url}")
                    break
                else:
                    logger.warning(
                        f"Auto-register attempt {reg_attempt} failed (HTTP {resp.status_code}): {resp.text}"
                    )
            except Exception as exc:
                logger.warning(f"Auto-register attempt {reg_attempt} error: {exc}")
            await asyncio.sleep(1.0)
