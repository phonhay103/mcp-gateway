# Enterprise Mock MCP Gateway

Hệ thống **Enterprise Dynamic Scenario MCP Gateway** chuẩn doanh nghiệp phát triển trên **Python 3.14**, hỗ trợ kiến trúc **Decoupled (Độc Lập)** giữa MCP Servers gốc và các Scenarios, nạp Tool Schemas tự động qua **FastMCP Native Protocol (`tools/list`)**, cùng hệ thống **Session Lifecycle RESTful API Endpoints**.

---

## 🐍 Yêu Cầu Môi Trường & Chuẩn Hóa Runtime

- **Python Version**: `3.14` (Cấu hình chuẩn hóa qua `.python-version`, `pyproject.toml` (`requires-python = ">=3.14"`), `Dockerfile` (`python:3.14-slim`), và `uv.lock`).
- **Package Manager**: `uv` (Fast Python package installer).
- **Core Frameworks**: `fastmcp>=3.4.4`, `pydantic>=2.10.0`, `starlette`, `loguru>=0.7.3`, `pyjwt>=2.10.0`.

---

## 🛠️ FastMCP Native MCP Protocol Tool Serving (`tools/list`)

Mọi FastMCP Server instance (Gateway SSE Endpoints: `/sysops/sse`, `/utility/sse`, `/full/sse` cũng như các Dedicated Ports: `8001`, `8002`, `8003`) **phục vụ chuẩn theo thức Model Context Protocol (MCP)**.

Khi MCP Client (LLM Client, Inspector, Orchestrator) kết nối và gửi yêu cầu chuẩn JSON-RPC `tools/list`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": {}
}
```

FastMCP sẽ tự động phản hồi lại danh mục các tool thuộc Scenario đó kèm theo đầy đủ JSON `inputSchema` được định nghĩa chính xác từ Python Type Annotations, `typing.Annotated` và Pydantic `Field(description=...)`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "manage_users",
        "description": "Session & Scenario isolated CRUD operations for user management.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "action": {
              "type": "string",
              "description": "User CRUD action ('list', 'get', 'create', 'delete')"
            },
            "user_id": {
              "anyOf": [{ "type": "string" }, { "type": "null" }],
              "default": null,
              "description": "Unique User ID (required for 'get' and 'delete' actions)"
            },
            "name": {
              "anyOf": [{ "type": "string" }, { "type": "null" }],
              "default": null,
              "description": "Full Name (required for 'create' action)"
            },
            "email": {
              "anyOf": [{ "type": "string" }, { "type": "null" }],
              "default": null,
              "description": "Email address (required for 'create' action)"
            },
            "role": {
              "type": "string",
              "default": "user",
              "description": "User access role (default: 'user')"
            }
          },
          "required": ["action"]
        }
      }
    ]
  }
}
```

---

## 📂 Static Tool JSON Schemas Catalog (`config/schemas/`)

Ngoài việc phục vụ trực tiếp qua giao thức FastMCP MCP Protocol, hệ thống duy trì các file JSON định nghĩa Tool Schemas phục vụ mục đích kiểm thử và xuất danh mục:
- `config/schemas/tools/echo.json`
- `config/schemas/tools/get_system_info.json`
- `config/schemas/tools/calculate.json`
- `config/schemas/tools/manage_users.json`
- `config/schemas/tools/http_ping.json`
- `config/schemas/tools/generate_random_data.json`
- `config/schemas/tools.json` (File danh mục tổng hợp)

---

## 🌐 Session & Scenario Lifecycle API Endpoints

Hệ thống cung cấp đầy đủ các HTTP RESTful Endpoints công khai giúp client (LLM Orchestrator, Benchmark Evaluation Harness) khởi tạo, truy vấn, gán scenario và đóng phiên làm việc:

| Endpoint | Method | Mô tả |
| :--- | :--- | :--- |
| `/scenarios` | `GET` | Liệt kê danh sách tất cả các Scenario khả dụng cùng công cụ Whitelisted |
| `/scenarios/select` | `POST` | Gán một `session_id` vào một `scenario_id` và nạp Fixtures ban đầu |
| `/scenarios/clear` | `POST` | Xóa dữ liệu bộ nhớ đệm (state/cache) của một Session |
| `/sessions` | `GET` | Liệt kê tất cả các Session đang hoạt động và Scenario đang gán |
| `/sessions` | `POST` | **Khởi tạo (Initialize) một Session mới** với Session ID & Scenario tùy chọn |
| `/sessions/{session_id}` | `DELETE` | **Đóng & hủy hoàn toàn (Destroy/Evict)** một Session khỏi bộ nhớ Gateway |
| `/sessions/close` | `POST` | Đóng Session thông qua JSON Body Payload |
| `/tools` | `GET` | RESTful mirror hỗ trợ định dạng MCP (`inputSchema`) & OpenAI (`parameters`) |

---

### 📝 Chi Tiết Sử Dụng Session Lifecycle Endpoints

#### 1. Khởi Tạo Session Mới (`POST /sessions`)
```bash
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "eval-session-999",
    "scenario_id": "utility-scenario",
    "metadata": {
      "client": "eval-harness",
      "benchmark": "mcp-v1"
    }
  }'
```

**Response (`201 Created`)**:
```json
{
  "status": "created",
  "session_id": "eval-session-999",
  "scenario_id": "utility-scenario",
  "created_at": "2026-07-24T03:00:00Z",
  "metadata": {
    "client": "eval-harness",
    "benchmark": "mcp-v1"
  }
}
```

---

#### 2. Liệt Kê Các Session Đang Hoạt Động (`GET /sessions`)
```bash
curl -X GET http://localhost:8000/sessions
```

---

#### 3. Đóng Phiên Làm Việc (`DELETE /sessions/{session_id}`)
Giải phóng hoàn toàn bộ nhớ cache/state thuộc về `session_id` đó:
```bash
curl -X DELETE http://localhost:8000/sessions/eval-session-999
```

---

## 🏛️ Architecture & Session Isolation (`X-Session-ID`)

- **3-Way State Key**: Cô lập dữ liệu tuyệt đối theo `session_id :: scenario_id :: server_id`.
- **Automatic Fixture Seeding**: Khởi tạo lại trạng thái ban đầu sạch sẽ khi một Session mới được tạo.
- **Concurrent Execution**: Hàng ngàn request có thể chạy song song với các `X-Session-ID` khác nhau mà không làm nhiễu hay rò rỉ dữ liệu của nhau.

---

## ⚡ Các Lệnh Makefile

| Lệnh | Mô tả |
| :--- | :--- |
| `make install` | Cài đặt các thư viện phụ thuộc bằng `uv` với Python 3.14 |
| `make test` | Chạy bộ kiểm thử tự động pytest (37 bài tests) trên Python 3.14 |
| `make run` / `make run-hybrid` | **Enterprise Hybrid Mode**: Chạy CẢ Port chung `8000` & Các Port Scenario riêng `8001..8003` |
| `make run-gateway` | Chạy chỉ **Port chung Gateway** (`port 8000`) |
| `make run-multi` | Chạy chỉ **Các Port riêng của từng Scenario** (`8001`, `8002`, `8003`) |
| `make up` | Khởi chạy Docker Compose Gateway ở background (Python 3.14 Container) |
| `make logs` | Xem logs container |
| `make down` | Dừng Docker Compose |
