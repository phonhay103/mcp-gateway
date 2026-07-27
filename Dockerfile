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

# Install standard curl for healthcheck or debug, and create a secure non-root user
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -g 10001 gateway && \
    useradd -u 10001 -g gateway -m -s /bin/bash gateway

# Copy virtual environment, source code, and default config
COPY --from=builder --chown=gateway:gateway /app/.venv /app/.venv
COPY --from=builder --chown=gateway:gateway /app/src /app/src
COPY --from=builder --chown=gateway:gateway /app/pyproject.toml /app/pyproject.toml
COPY --from=builder --chown=gateway:gateway /app/config /app/config

ENV PATH="/app/.venv/bin:$PATH" \
    MCP_CONFIG_PATH="config/mcp_servers.yaml" \
    PYTHONUNBUFFERED=1

# Run container as a secure, non-privileged user (Zero-Trust Compliant)
USER gateway

EXPOSE 2026

# Default entry point
CMD ["mcp-gateway"]
