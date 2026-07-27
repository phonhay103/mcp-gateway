# Standalone Mock MCP Service (`mock_mcp`)

Dịch vụ **Mock MCP Server độc lập** (Standalone Microservice) phục vụ giả lập kịch bản (Scenarios), nạp dữ liệu mẫu (Fixtures), bơm lỗi hạ tầng thực tế (Chaos Fault Injection Engine), chụp ảnh nhanh trạng thái dữ liệu (Time-Travel State Snapshots), ghi vết cuộc gọi (Execution Trace Recording), và cung cấp công cụ tìm kiếm ngữ nghĩa công cụ (Meta-Tools).

---

## 🏛️ Kiến Trúc Microservice Độc Lập

`mock_mcp` hoàn toàn không phụ thuộc vào `mcp_gateway`. Dịch vụ hoạt động trên cổng riêng (mặc định `:2027`), tiếp nhận các cuộc gọi MCP JSON-RPC tại `/mcp`, và sử dụng các HTTP Headers (`X-Session-ID`, `X-MCP-Scenario`) để phân giải ngữ cảnh phiên và kịch bản tương ứng.

```
Client / Gateway ---> Streamable HTTP POST /mcp (Headers: X-Session-ID, X-MCP-Scenario)
                           |
                           v
                   [SessionMiddleware]
                           |
            +--------------+--------------+
            |                             |
            v                             v
   [Session & Fixtures]          [Chaos Fault Engine]
  (State, Snapshots, Users)     (Latency, 429, 502/503/504)
```

---

## 🛠️ Các Tính Năng Chi Tiết

### 1. Dynamic Scenarios & Fixture State Loading
- **Cấu hình Kịch bản (`config/scenarios/*.yaml`)**: Định nghĩa danh sách các kịch bản kiểm thử (ví dụ: `sysops-scenario`, `utility-scenario`, `full-scenario`).
- **Nạp Dữ Liệu Mẫu (`config/fixtures/*.yaml`)**: Tự động khởi tạo trạng thái bộ nhớ ban đầu cho phiên làm việc.
- **Mô phỏng Lỗi Công Cụ (Tool Behaviors & Error Simulations)**: Trả về kết quả mock hoặc ép phản hồi lỗi chuẩn MCP (`isError: true`, code, message, details).

### 2. Quản Lý Phiên & Cách Ly Trạng Thái (3-Way Context Isolation)
- **HTTP Headers**:
  - `X-Session-ID`: Định danh phiên làm việc (Tự động sinh UUID v7 nếu không được truyền vào).
  - `X-MCP-Scenario`: Chỉ định kịch bản vận hành cần kích hoạt.
- Đảm bảo việc chạy song song nhiều kịch bản và phiên làm việc không bị rò rỉ dữ liệu chéo.

### 3. Engine Bơm Lỗi Hạ Tầng Thực Tế (Chaos Fault Injection)
- Mẫu hình lỗi hỗ trợ:
  - `latency`: Tạo độ trễ ngẫu nhiên hoặc cố định trước khi xử lý.
  - `error_rate`: Bơm lỗi ngẫu nhiên theo tỷ lệ phần trăm (0.0 - 1.0).
  - `http_status`: Giả lập phản hồi lỗi HTTP 429 (Rate Limit Exceeded), 502 Bad Gateway, 503 Service Unavailable, 504 Gateway Timeout.
  - `malformed_json`: Trả về JSON hỏng để kiểm thử khả năng xử lý exception của client.
  - `schema_drift`: Làm lệch cấu trúc kết quả trả về so với JSON Schema đã đăng ký.

### 4. Snapshots Trạng Thái & Trace Recording
- **Time-Travel Snapshots**:
  - `create_snapshot(label)`: Chụp ảnh trạng thái bộ nhớ dữ liệu tại mốc thời gian.
  - `restore_snapshot(snapshot_id)`: Khôi phục trạng thái về ảnh chụp đã lưu.
  - `diff_snapshots(snap1, snap2)`: So sánh sai lệch dữ liệu giữa 2 mốc thời gian.
- **Trace Recorder**:
  - Tự động lưu vết lịch sử mọi cuộc gọi tool, tham số truyền vào, kết quả phản hồi, thời gian xử lý, và SHA-256 hash của state.

### 5. Công Cụ Dynamic Discovery Meta-Tools
- **`find_tools(query, top_k=3)`**: Tìm kiếm ngữ nghĩa danh sách công cụ theo từ khóa mô tả. Giảm kích thước prompt từ >30k tokens xuống ~1.3k tokens.
- **`describe_tools(tool_names)`**: Truy vấn JSON Schema chi tiết của công cụ cụ thể khi LLM cần gọi.

---

## ⚡ Lệnh Khởi Chạy & Cấu Hình

### 1. Khởi chạy trực tiếp qua `uv`
```bash
uv run mock-mcp --config config/mcp_servers.yaml
```

### 2. Khởi chạy qua Makefile
```bash
make run-mock
```

### 3. Biến Môi Trường (Environment Variables)

| Biến môi trường | Mặc định | Mô tả |
| :--- | :--- | :--- |
| `MCP_CONFIG_PATH` | `config/mcp_servers.yaml` | Đường dẫn tới tệp cấu hình YAML |
| `LOG_LEVEL` | `INFO` | Mức độ ghi loguru (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `GATEWAY_REGISTER_URL` | *(None)* | URL của `mcp_gateway` (vd: `http://mcp-gateway:2026`) để bật tự động đăng ký |
| `MOCK_SERVICE_URL` | `http://mock-mcp:2027` | URL của dịch vụ `mock_mcp` khai báo với Gateway |
| `GATEWAY_API_KEY` | `admin-secret-key-123` | API Key xác thực khi đăng ký với Gateway |
