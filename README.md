# Enterprise Mock MCP Gateway & Real-World Bug Reproduction Engine

Hệ thống **Enterprise Dynamic Scenario MCP Gateway** phát triển trên **Python 3.14**, tích hợp công cụ **Chẩn Đoán & Tái Hiện Bug Thực Tế (Real-World Debug & Deterministic Reproduction Engine)** cho phép ghi vết trace, tái hiện bug chuẩn xác, giả lập lỗi hạ tầng thực tế (Chaos Engineering), và du hành thời gian trạng thái (Time-Travel Debugging).

---

## 🐍 Yêu Cầu Môi Trường & Chuẩn Hóa Runtime

- **Python Version**: `3.14` (Cấu hình chuẩn hóa qua `.python-version`, `pyproject.toml` (`requires-python = ">=3.14"`), `Dockerfile` (`python:3.14-slim`), và `uv.lock`).
- **Package Manager**: `uv` (Fast Python package installer).
- **Core Frameworks**: `fastmcp>=3.4.4`, `pydantic>=2.10.0`, `starlette`, `loguru>=0.7.3`, `pyjwt>=2.10.0`.

---

## 🐞 Tính Năng Chẩn Đoán & Tái Hiện Bug Thực Tế (Real-World Debug Engine)

1. **Deterministic Execution Trace Recording (`recorder.py`)**: Ghi vết thời gian thực mọi bước gọi công cụ JSON-RPC, tham số đầu vào, kết quả trả về, độ trễ `duration_ms`, và mã băm SHA-256 biến đổi trạng thái trước/sau từng bước.
2. **Chaos & Real-World Fault Injection Engine (`chaos.py`)**: Giả lập các sự cố hạ tầng thực tế một cách xác định hoặc xác suất:
   - `latency`: Tạo độ trễ mạng thực tế.
   - `error_rate`: Tỷ lệ lỗi ngẫu nhiên hoặc 100%.
   - `http_status`: Giả lập các lỗi HTTP thực tế (429 Rate Limit, 502 Bad Gateway, 503 Unavailable, 504 Timeout).
   - `malformed_json`: Trả về JSON bị hỏng để kiểm thử khả năng chống chịu của client.
   - `schema_drift`: Giả lập lệch chuẩn schema API backend.
3. **Time-Travel State Snapshots (`session.py`)**: Cho phép chụp ảnh nhanh trạng thái (`create_snapshot`), khôi phục trạng thái về quá khứ (`restore_snapshot`), và so sánh chênh lệch (`diff_snapshots`).
4. **Self-Contained Repro Bundle (`.mcp-repro.json`)**: Xuất toàn bộ kịch bản lỗi, cấu hình môi trường, quy tắc chaos, và vết gọi thành 1 tệp gói gọn phục vụ đính kèm Issue, CI/CD Automated Replay, hoặc gửi cho AI Debug Agent.
5. **Deterministic Replay & Drift Analysis Engine (`ReplayEngine`)**: Tái chạy lại chính xác vết cuộc gọi cũ step-by-step, tự động so sánh kết quả mong đợi vs kết quả thực tế để phát hiện hiện tượng lệch dữ liệu (Data/Result Drift).

---

## 🛡️ Các Tính Năng An Ninh & Quản Trị Doanh Nghiệp (Enterprise Security)

1. **SHA-256 Schema Hash-Pinning & Anti-Rug-Pull**: Tự động gán băm SHA-256 cho từng tool schema và phát hiện thay đổi ngầm.
2. **Tool Description Sanitizer (Anti-Tool Poisoning)**: Tự động lọc bỏ các thẻ chèn lời nhắc độc hại khỏi mô tả công cụ.
3. **Per-Call JSON-RPC Tool Authorization**: Phân quyền thực thi từng phương thức tool (`allowed_tools` pattern matching).
4. **Cryptographically Signed Audit Records**: Mọi sự kiện kiểm toán được ký số HMAC-SHA256 bất biến.
5. **DNS Rebinding Protection**: Kiểm tra tiêu đề `Origin` trên các kết nối Streamable HTTP.

---

## ⚡ Các Tính Năng Tối Ưu Hiệu Năng & Ngữ Cảnh

1. **Dynamic Tool Discovery Meta-Tools (`find_tools`, `describe_tools`)**: Giảm token đầu vào cho LLM context window từ >30.000 token xuống còn ~1.300 token.
2. **Code Mode Sandboxed Execution (`execute_tool_code`, ...)**: Thực thi kịch bản lọc/biến đổi dữ liệu trực tiếp tại Sandbox Gateway, giảm tới 92.8% token kết quả thô.
3. **Server Multiplexing (`/scenarios/multiplexed/tools`)**: Hợp nhất danh mục công cụ với tiền tố không gian tên (`sysops__manage_users`).
4. **Prometheus / OpenTelemetry Metrics (`/metrics`)**: Xuất chỉ số đo lường chuẩn Prometheus.
5. **STDIO Protocol Bridge Adapter (`STDIOBridgeAdapter`)**: Chuyển đổi hai chiều giữa STDIO và Streamable HTTP.

---

## 🌐 Debugging & Real-World Reproduction Endpoints

| Endpoint | Method | Mô tả |
| :--- | :--- | :--- |
| `/sessions/{session_id}/trace` | `GET` | Xem toàn bộ lịch sử trace cuộc gọi tool và thay đổi trạng thái |
| `/sessions/{session_id}/repro` | `GET` | **Tải về gói tái hiện bug tự chứa** (`.mcp-repro.json`) |
| `/repro/replay` | `POST` | **Import và phát lại kịch bản bug** đính kèm phân tích Drift |
| `/scenarios/{scenario_id}/chaos` | `POST` / `DELETE` | **Cấu hình / Xóa quy tắc bơm lỗi Chaos Injection** |
| `/sessions/{session_id}/snapshots` | `POST` | **Chụp ảnh snapshot thời gian** trạng thái session |
| `/sessions/{session_id}/snapshots/restore` | `POST` | **Du hành thời gian khôi phục trạng thái** về snapshot cũ |
| `/metrics` | `GET` | Xuất chỉ số đo lường Prometheus |
| `/scenarios` | `GET` | Liệt kê danh sách tất cả Scenario |
| `/scenarios/multiplexed/tools` | `GET` | Hợp nhất danh mục công cụ Multiplexed |

---

## 🛠️ Debugging Meta-Tools cho AI Agent & Developers

Hệ thống cung cấp sẵn các meta-tools trực tiếp qua giao thức MCP:
- `get_execution_trace`: Truy vấn lịch sử vết gọi tool của session.
- `inject_fault_rule`: Bơm quy tắc lỗi (độ trễ, HTTP status, lỗi JSON).
- `clear_fault_rules`: Dọn dẹp quy tắc lỗi.
- `create_session_snapshot`: Chụp snapshot trạng thái.
- `restore_session_snapshot`: Khôi phục trạng thái.
- `diff_session_snapshots`: So sánh biến đổi trạng thái giữa 2 checkpoint.
- `export_repro_bundle`: Xuất tệp gói gọn `.mcp-repro.json`.
- `replay_session_trace`: Tái chạy kịch bản trace và phân tích sai lệch.

---

## ⚡ Các Lệnh Makefile

| Lệnh | Mô tả |
| :--- | :--- |
| `make install` | Cài đặt các thư viện phụ thuộc bằng `uv` với Python 3.14 |
| `make test` | Chạy bộ kiểm thử tự động pytest (53 bài tests) trên Python 3.14 |
| `make run` / `make run-hybrid` | Enterprise Hybrid Mode (Port 8000 + Ports 8001..8003) |
| `make run-gateway` | Chạy Port chung Gateway (`port 8000`) |
| `make up` | Khởi chạy Docker Compose Gateway ở background |
