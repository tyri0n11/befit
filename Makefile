.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE_BASE := docker/docker-compose.yaml
COMPOSE_DEV  := docker/docker-compose.dev.yaml
COMPOSE_PROD := docker/docker-compose.prod.yaml
DC_DEV  := docker compose --env-file .env -f $(COMPOSE_BASE) -f $(COMPOSE_DEV)
DC_PROD := docker compose --env-file .env -f $(COMPOSE_BASE) -f $(COMPOSE_PROD)

SERVICE ?= api

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.env: ## Create .env from .env.example if missing
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")

.PHONY: dev
dev: .env ## Build + run the full local stack with autoreload (foreground)
	$(DC_DEV) up --build

.PHONY: up
up: .env ## Start the local stack detached
	$(DC_DEV) up --build -d

.PHONY: down
down: ## Stop the local stack
	$(DC_DEV) down

.PHONY: clean
clean: ## Stop the local stack and delete its volumes
	$(DC_DEV) down -v --remove-orphans

.PHONY: restart
restart: down up ## Restart the local stack

.PHONY: logs
logs: ## Tail logs (SERVICE=api by default)
	$(DC_DEV) logs -f $(SERVICE)

.PHONY: ps
ps: ## Show container status
	$(DC_DEV) ps

.PHONY: sh
sh: ## Open a shell in a running container (SERVICE=api)
	$(DC_DEV) exec $(SERVICE) bash

.PHONY: psql
psql: ## Open psql against the dev database
	$(DC_DEV) exec postgres psql -U $${DB_USER:-befit} -d $${DB_NAME:-befit}

.PHONY: db-init
db-init: ## Re-run scripts/database/*.sql against the running dev database
	@set -euo pipefail; \
	for f in $(sort $(wildcard scripts/database/*.sql)); do \
		echo "==> $$f"; \
		$(DC_DEV) exec -T postgres \
			psql -v ON_ERROR_STOP=1 -U $${DB_USER:-befit} -d $${DB_NAME:-befit} < "$$f"; \
	done

.PHONY: seed
seed: .env ## Seed catalog master data from scripts/data/*.yaml
	uv run python -m scripts.seed

.PHONY: seed-dry
seed-dry: .env ## Validate the seed data and roll the transaction back
	uv run python -m scripts.seed --dry-run

.PHONY: seed-dev-account
seed-dev-account: .env ## Seed a sample local-dev account with workout history
	uv run python -m scripts.seed_dev_account

.PHONY: db-reset
db-reset: ## Drop the postgres volume and recreate the schema from scratch
	$(DC_DEV) rm -sfv postgres
	docker volume rm -f befit_postgres-data
	$(DC_DEV) up -d postgres

.PHONY: redis-cli
redis-cli: ## Open redis-cli against the dev cache
	$(DC_DEV) exec redis redis-cli

.PHONY: build
build: ## Build images without starting them
	$(DC_DEV) build

.PHONY: install
install: ## Sync local (host) virtualenv with uv
	uv sync

.PHONY: run
run: .env ## Run the API on the host (no containers; needs postgres/redis running)
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port $${API_PORT:-8000}

.PHONY: lint
lint: ## Check lint rules + formatting (no files written)
	uv run ruff check .
	uv run ruff format --check .

.PHONY: fmt
fmt: ## Autofix lint violations and reformat
	uv run ruff check --fix .
	uv run ruff format .

.PHONY: test
test: ## Run the test suite inside the api container
	$(DC_DEV) run --rm $(SERVICE) pytest

.PHONY: prod
prod: .env ## Run the production stack locally
	$(DC_PROD) up --build -d
