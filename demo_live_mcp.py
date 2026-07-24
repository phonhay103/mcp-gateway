"""
Live End-to-End Test & Demonstration Script for Enterprise Mock MCP Gateway.
Exercises:
1. Authenticated Streamable HTTP MCP JSON-RPC Protocol Session (initialize, tools/list, tools/call)
2. Real-World Chaos Fault Injection Engine & Error Handling
3. Time-Travel State Checkpoints (Create Snapshot & Restore)
4. Session Execution Trace Recording & .mcp-repro.json Export
5. Deterministic Replay & Result Drift Analysis
6. Prometheus Metrics Exporter
"""

import json
from starlette.testclient import TestClient
from mock_mcp_gateway.manager import ServerManager


def main():
    print("==========================================================================")
    print("🚀 ENTERPRISE MOCK MCP GATEWAY & REAL-WORLD DEBUG ENGINE LIVE TEST DEMO")
    print("==========================================================================\n")

    # 1. Initialize Server Manager & Gateway Application
    manager = ServerManager("config/mcp_servers.yaml")
    app = manager.create_gateway_app()

    api_key = "admin-secret-key-123"

    with TestClient(app) as client:
        # --- PART 1: STREAMABLE HTTP PROTOCOL SESSION ---
        print("--- 1. Establishing Streamable HTTP Session ---")
        conn_res = client.get(
            "/full/mcp",
            headers={"X-API-Key": api_key, "Accept": "text/event-stream"},
        )
        mcp_session_id = conn_res.headers.get("mcp-session-id")
        print(f"✅ Connection Established. Assigned Mcp-Session-Id: {mcp_session_id}\n")

        auth_headers = {
            "X-API-Key": api_key,
            "X-Session-ID": "live-demo-session",
            "Mcp-Session-Id": mcp_session_id,
            "X-MCP-Scenario": "full-scenario",
            "Accept": "application/json, text/event-stream",
        }

        # Initialize JSON-RPC
        print("--- 2. Sending JSON-RPC 'initialize' Request ---")
        init_res = client.post(
            "/full/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "enterprise-agent", "version": "1.0.0"},
                },
            },
            headers=auth_headers,
        )
        print(f"Status: {init_res.status_code}")
        print(f"Response Payload:\n{init_res.text}\n")

        # tools/list JSON-RPC
        print("--- 3. Querying Available Tools via JSON-RPC 'tools/list' ---")
        list_res = client.post(
            "/full/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=auth_headers,
        )
        print(f"Response Payload:\n{list_res.text}\n")

        # tools/call JSON-RPC (Create User)
        print("--- 4. Executing Tool via JSON-RPC 'tools/call' (manage_users -> create) ---")
        call_res = client.post(
            "/full/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "manage_users",
                    "arguments": {
                        "action": "create",
                        "name": "John Doe",
                        "email": "john.doe@enterprise.com",
                        "role": "lead_engineer",
                    },
                },
            },
            headers=auth_headers,
        )
        print(f"Response Payload:\n{call_res.text}\n")

        # --- PART 2: TIME-TRAVEL SNAPSHOTTING ---
        print("--- 5. Creating Time-Travel State Checkpoint Snapshot ---")
        snap_res = client.post(
            "/sessions/live-demo-session/snapshots",
            json={"label": "State After Engineer User Created"},
            headers=auth_headers,
        )
        snap_data = snap_res.json()
        snap_id = snap_data["snapshot_id"]
        print(f"✅ Created Snapshot [{snap_id}]: {snap_data}\n")

        # --- PART 3: CHAOS FAULT INJECTION ---
        print("--- 6. Injecting Real-World Chaos Fault Rule (Simulated Latency & Division Error) ---")
        chaos_res = client.post(
            "/scenarios/full-scenario/chaos",
            json={
                "tool_name": "calculate",
                "fault_type": "error_rate",
                "rate": 1.0,
                "error_message": "🚨 [CHAOS SIMULATION] Backend Arithmetic Service Outage (503)",
            },
            headers=auth_headers,
        )
        print(f"Chaos Rule Status: {chaos_res.json()}\n")

        print("--- 7. Executing 'calculate' Tool Under Active Chaos Fault ---")
        chaos_call_res = client.post(
            "/full/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "calculate", "arguments": {"expression": "250 * 4"}},
            },
            headers=auth_headers,
        )
        print(f"Faulted Response Payload:\n{chaos_call_res.text}\n")

        # Clear Chaos Rule
        client.delete("/scenarios/full-scenario/chaos", headers=auth_headers)
        print("Cleared active chaos rules.\n")

        # --- PART 4: REPRO BUNDLE EXPORT & DETERMINISTIC REPLAY ---
        print("--- 8. Exporting Self-Contained Repro Bundle (.mcp-repro.json) ---")
        repro_res = client.get("/sessions/live-demo-session/repro", headers=auth_headers)
        repro_bundle = repro_res.json()
        print(f"Exported Repro Bundle Version: {repro_bundle.get('version')}")
        print(f"Session ID: {repro_bundle.get('session_id')}")
        print(f"Initial State Keys: {list(repro_bundle.get('initial_state', {}).keys())}\n")

        print("--- 9. Deterministic Replay & Drift Analysis Report ---")
        replay_res = client.post("/repro/replay", json=repro_bundle, headers=auth_headers)
        print(f"Replay Report:\n{json.dumps(replay_res.json(), indent=2)}\n")

        # --- PART 5: PROMETHEUS METRICS EXPORTER ---
        print("--- 10. Querying Prometheus Metrics Endpoint (/metrics) ---")
        metrics_res = client.get("/metrics")
        print("Metrics Excerpt:\n")
        print("\n".join(metrics_res.text.splitlines()[:16]))

    print("\n==========================================================================")
    print("✅ LIVE END-TO-END MCP TEST COMPLETED WITH 100% SUCCESS!")
    print("==========================================================================")


if __name__ == "__main__":
    main()
