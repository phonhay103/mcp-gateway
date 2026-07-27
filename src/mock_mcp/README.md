# Standalone Unified Mock MCP Service (`mock_mcp`)

The **Standalone Unified Mock MCP Service** provides scenario simulation for enterprise testing, fixture loading, tool-level chaos and error simulation, and a comprehensive REST Management API outside the core MCP protocol.

---

## 🏛️ Unified Microservice Architecture

`mock_mcp` operates completely independently of `mcp_gateway`. The service runs on a dedicated port (default `:2027`) and exposes a single unified MCP JSON-RPC endpoint at `/mcp`.

```
                  +-----------------------------------+
                  |        Mock MCP App (Starlette)   |
                  +-----------------------------------+
                                    |
          +-------------------------+-------------------------+
          |                                                   |
          v                                                   v
  [MCP Endpoint: /mcp]                              [REST Control Plane]
  - Single endpoint routing.                         - GET /scenarios (List scenarios)
  - Isolated via X-MCP-Scenario header               - GET /scenarios/{id} (Details)
    or default active scenario.                      - POST /scenarios/{id}/activate (Activate)
  - Dynamic tool filtering per scenario.             - GET/POST /scenarios/{id}/state (In-memory DB)
  - Rejects unauthorized tool calls.                 - POST /scenarios/{id}/reset (Restore YAML)
```

---

## 🛠️ Detailed Features

### 1. Flexible Scenario ID Resolution (No Path Prefixes)
Rather than splitting routes into separate paths like `/full/mcp` or `/sysops/mcp`, Mock MCP centralizes all traffic into **a single `/mcp` connection point**.
* **Active Identification:** Reads the incoming `X-MCP-Scenario` header to dynamically resolve the corresponding scenario workflow (`full-scenario`, `sysops-scenario`, `utility-scenario`, `chaos-scenario`).
* **Default Scenario:** If no header is provided, Mock MCP falls back to the configured default scenario (or the one activated via the REST API).

### 2. Static Definitions & Strict Tool Limiting per Scenario
Each scenario defines a static `tools:` list restricting execution scope:
* **`tools/list`**: Returns a dynamically filtered JSON Schema list containing only tools enabled for the active scenario.
* **`tools/call`**: Enforces strict security bounds. If a client attempts to invoke a tool outside the scenario's permission scope, the server rejects it with standard JSON-RPC error code `-32601`.

### 3. Independent REST Control Plane API
Mock MCP provides auxiliary REST APIs for state management, continuous integration (CI/CD) automation, and system integration:

| Method | Path | Description |
| :--- | :--- | :--- |
| **GET** | `/scenarios` | List all scenarios, permitted tools, linked fixtures, and default activation status. |
| **GET** | `/scenarios/{id}` | Retrieve detailed configuration parameters for a specific scenario. |
| **POST** | `/scenarios/{id}/activate` | Set the scenario as the system-wide default (when incoming MCP requests omit `X-MCP-Scenario`). |
| **GET** | `/scenarios/{id}/state` | Inspect the current in-memory state/fixtures of the scenario (e.g., mock users, metrics). |
| **POST** | `/scenarios/{id}/state` | Update or patch dynamic mock state for boundary and dynamic test conditions. |
| **POST** | `/scenarios/{id}/reset` | Reload initial mock data from original YAML fixtures in the configuration directory. |

### 4. Fixture Loading & MCP-Compliant Fault Injection
* **Fixture Data:** Automatically loads YAML files from the external configuration directory (`configs/mock_mcp/fixtures/default_users.yaml`, `sysops_metrics.yaml`) into independent in-memory states per scenario.
* **Tool-Level Chaos & Error Simulation:** Supports declaring latency delays (`latency_ms`), error rates (`error_rate`), simulated HTTP status codes (`http_status`), and explicit error definitions (`error`) per tool inside scenario configurations.

---

## ⚡ Startup & Configuration

### 1. Externalized Configurations (`configs/`)
All Mock MCP configurations reside in the root-level `/configs/mock_mcp/` directory rather than inside `src/`:
* `configs/mock_mcp/mock_config.yaml`: Core server configuration (port, fixtures directory, scenarios directory).
* `configs/mock_mcp/scenarios/*.yaml`: Detailed testing scenarios (`full`, `sysops`, `utility`, `chaos`).
* `configs/mock_mcp/fixtures/*.yaml`: Initial mock state datasets.

### 2. Run Directly via `uv`
```bash
uv run mock-mcp --config configs/mock_mcp/mock_config.yaml
```

### 3. Run via Makefile
```bash
make run-mock
```

### 4. Environment Variables

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `MCP_CONFIG_PATH` | `configs/mock_mcp/mock_config.yaml` | Path to the external YAML configuration file |
| `LOG_LEVEL` | `INFO` | Loguru logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `GATEWAY_REGISTER_URL` | *(None)* | URL of `mcp_gateway` (e.g., `http://mcp-gateway:2026`) to enable auto-registration |
| `MOCK_SERVICE_URL` | `http://mock-mcp:2027` | URL of the `mock_mcp` service advertised to the gateway |
| `GATEWAY_API_KEY` | `admin-secret-key-123` | API key for authenticating auto-registration with the gateway |
