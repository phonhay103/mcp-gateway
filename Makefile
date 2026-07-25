.PHONY: help install test run run-gateway run-multi run-hybrid run-streamable build up down logs restart clean

# Default target
.DEFAULT_GOAL := help

help: ## Show available commands
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies using uv
	uv sync

test: ## Run unit tests with pytest
	uv run pytest -v

run: run-hybrid ## Run in Enterprise Hybrid mode (Shared Gateway + Dedicated Ports)

run-gateway: ## Run multi-MCP in Shared Gateway mode (port 8000 only)
	uv run mcp-gateway --mode gateway --config config/mcp_servers.yaml

run-multi: ## Run multi-MCP on Dedicated Ports only (8001, 8002, 8003...)
	uv run mcp-gateway --mode multi-port --config config/mcp_servers.yaml

run-hybrid: ## Run BOTH Shared Gateway (port 8000) AND Dedicated Ports (8001, 8002...)
	uv run mcp-gateway --mode hybrid --config config/mcp_servers.yaml

run-streamable: ## Run single MCP server in HTTP Streamable / SSE mode
	uv run mcp-gateway --mode single --transport streamable-http

build: ## Build Docker container image
	docker compose build

up: ## Start multi-MCP gateway in background with Docker Compose
	docker compose up -d

down: ## Stop Docker Compose services
	docker compose down

logs: ## View Docker Compose logs
	docker compose logs -f

restart: ## Restart Docker Compose services
	docker compose restart

clean: ## Remove temporary build files and caches
	rm -rf .pytest_cache .venv dist build *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} +
