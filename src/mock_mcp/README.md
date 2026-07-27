# Standalone Unified Mock MCP Service (`mock_mcp`)

Dịch vụ **Mock MCP Server thống nhất** (Standalone Unified Microservice) phục vụ giả lập kịch bản (Scenarios) kiểm thử doanh nghiệp, nạp dữ liệu mẫu (Fixtures), kiểm thử mô phỏng lỗi (Error Simulations), và cung cấp API quản trị trạng thái (REST Management API) toàn diện bên ngoài giao thức MCP.

---

## 🏛️ Kiến Trúc Microservice Thống Nhất

`mock_mcp` hoạt động độc lập hoàn toàn với `mcp_gateway`. Dịch vụ vận hành trên một cổng duy nhất (mặc định `:2027`) và cung cấp duy nhất một đường dẫn MCP JSON-RPC tại `/mcp`.

```
                  +-----------------------------------+
                  |        Mock MCP App (Starlette)   |
                  +-----------------------------------+
                                    |
          +-------------------------+-------------------------+
          |                                                   |
          v                                                   v
  [MCP Endpoint: /mcp]                              [REST Control Plane]
  - Định tuyến duy nhất.                             - GET /scenarios (Danh sách kịch bản)
  - Phân tách qua header X-MCP-Scenario             - GET /scenarios/{id} (Chi tiết)
    hoặc Scenario kích hoạt mặc định.               - POST /scenarios/{id}/activate (Kích hoạt)
  - Lọc động tools list theo đúng kịch bản.         - GET/POST /scenarios/{id}/state (In-memory DB)
  - Chặn cuộc gọi tools không được phép.            - POST /scenarios/{id}/reset (Khôi phục YAML)
```

---

## 🛠️ Các Tính Năng Chi Tiết

### 1. Phân Tách Scenario ID Linh Hoạt (Không Dùng Path Prefix)
Không còn chia tách đường dẫn thành `/full/mcp`, `/sysops/mcp`, Mock MCP tập trung toàn bộ dữ liệu vào **một đầu kết nối `/mcp`**.
* **Định danh chủ động:** Đọc header `X-MCP-Scenario` truyền vào để tự động phân giải luồng kịch bản tương ứng (`full-scenario`, `sysops-scenario`, `utility-scenario`).
* **Kịch bản mặc định:** Nếu không truyền Header, Mock MCP sẽ lấy kịch bản mặc định được cấu hình (hoặc kịch bản được kích hoạt thông qua REST API).

### 2. Định Nghĩa Tĩnh & Giới Hạn Tools Nghiêm Ngặt Theo Scenario
Mỗi Scenario định nghĩa một danh sách `tools:` tĩnh, giới hạn phạm vi truy xuất:
* **`tools/list`**: Trả về danh sách JSON Schema đã lọc động, chỉ chứa những tools có tên trong danh sách của Scenario hiện hành.
* **`tools/call`**: Đảm bảo an toàn bảo mật. Nếu client cố tình gọi một công cụ nằm ngoài quyền truy cập của Scenario, server sẽ trả về lỗi JSON-RPC chuẩn `-32601` báo lỗi công cụ không được kích hoạt trong scenario hiện hành.

### 3. REST Control Plane API Độc Lập Ngoài Giao Thức MCP
Mock MCP cung cấp các REST API hữu dụng cho việc quản lý trạng thái, tự động hóa kiểm thử liên tục (CI/CD) và tích hợp hệ thống:

| Phương thức | Đường dẫn | Mô tả |
| :--- | :--- | :--- |
| **GET** | `/scenarios` | Liệt kê toàn bộ kịch bản, các tools được phép, fixtures liên kết, và trạng thái kích hoạt mặc định. |
| **GET** | `/scenarios/{id}` | Lấy chi tiết thông số của kịch bản cụ thể. |
| **POST** | `/scenarios/{id}/activate` | Đặt kịch bản này làm kịch bản mặc định toàn hệ thống (khi cuộc gọi MCP không gửi header `X-MCP-Scenario`). |
| **GET** | `/scenarios/{id}/state` | Xem dữ liệu trong bộ nhớ (in-memory state/fixtures) hiện tại của kịch bản (ví dụ: danh sách users giả lập, metrics). |
| **POST** | `/scenarios/{id}/state` | Cập nhật, chèn thêm dữ liệu giả lập động vào bộ nhớ phục vụ kiểm thử biên hoặc điều kiện kiểm thử động. |
| **POST** | `/scenarios/{id}/reset` | Reload nguyên bản dữ liệu mẫu từ các tệp YAML Fixtures ban đầu trong thư mục cấu hình. |

### 4. Nạp Dữ Liệu Fixtures & Mô Phỏng Lỗi Chuẩn MCP
* **Dữ liệu Fixtures:** Tự động nạp các tệp YAML từ thư mục ngoài hệ thống cấu hình (`configs/mock_mcp/fixtures/default_users.yaml`, `sysops_metrics.yaml`) vào in-memory state độc lập của từng Scenario.
* **Mô phỏng lỗi (`error_simulations`):** Hỗ trợ khai báo mô phỏng lỗi công cụ cụ thể trực tiếp từ tệp YAML, tự động ném ra lỗi MCP chuẩn có `isError: true` và mã lỗi kèm mô tả chi tiết.

---

## ⚡ Lệnh Khởi Chạy & Cấu Hình

### 1. Cấu hình nằm ngoài mã nguồn (`configs/`)
Mọi tệp tin cấu hình của Mock MCP nằm biệt lập tại thư mục gốc `/configs/mock_mcp/` thay vì trộn lẫn trong `src/`:
* `configs/mock_mcp/mock_config.yaml`: Cấu hình chung cổng chạy, thư mục fixtures, thư mục kịch bản.
* `configs/mock_mcp/scenarios/*.yaml`: Định nghĩa chi tiết các kịch bản kiểm thử (`full`, `sysops`, `utility`).
* `configs/mock_mcp/fixtures/*.yaml`: Định nghĩa dữ liệu ban đầu.

### 2. Khởi chạy trực tiếp qua `uv`
```bash
uv run mock-mcp --config configs/mock_mcp/mock_config.yaml
```

### 3. Khởi chạy qua Makefile
```bash
make run-mock
```

### 4. Biến Môi Trường (Environment Variables)

| Biến môi trường | Mặc định | Mô tả |
| :--- | :--- | :--- |
| `MCP_CONFIG_PATH` | `configs/mock_mcp/mock_config.yaml` | Đường dẫn tới tệp cấu hình YAML ngoài mã nguồn |
| `LOG_LEVEL` | `INFO` | Mức độ ghi loguru (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `GATEWAY_REGISTER_URL` | *(None)* | URL của `mcp_gateway` (vd: `http://mcp-gateway:2026`) để bật tự động đăng ký |
| `MOCK_SERVICE_URL` | `http://mock-mcp:2027` | URL của dịch vụ `mock_mcp` khai báo với Gateway |
| `GATEWAY_API_KEY` | `admin-secret-key-123` | API Key xác thực khi tự động đăng ký với Gateway |
