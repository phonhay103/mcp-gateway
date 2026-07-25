# Build stage using uv for fast dependency installation
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=python3.14

WORKDIR /app

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy application source and install project
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Final runtime stage
FROM python:3.14-slim

WORKDIR /app

# Copy virtual environment, source code, and default config
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/pyproject.toml /app/pyproject.toml
COPY --from=builder /app/config /app/config

ENV PATH="/app/.venv/bin:$PATH" \
    MCP_MODE="gateway" \
    MCP_CONFIG_PATH="config/mcp_servers.yaml" \
    PYTHONUNBUFFERED=1

EXPOSE 8000 8001 8002 8003

# Default entry point
CMD ["mcp-gateway"]
