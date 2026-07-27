.PHONY: help install test run run-gateway run-mock build up down logs restart clean

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

run: run-gateway ## Run Enterprise Shared Gateway on port 2026

run-gateway: ## Run Enterprise Gateway (port 2026)
	uv run mcp-gateway

run-mock: ## Run Standalone Mock MCP Service (port 2027)
	uv run mock-mcp --config src/mock_mcp/config/mcp_servers.yaml

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
