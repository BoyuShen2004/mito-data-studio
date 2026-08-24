PYTHON ?= python
NPM ?= npm
COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help setup dev db-up db-down docker-dev-up docker-dev-down docker-detect migrate check test test-backend test-frontend build check-git

help: ## Show the available development commands.
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Validate the environment, install frontend packages, and migrate.
	scripts/dev/setup.sh

dev: ## Run Django and Vite with live reload.
	scripts/dev/run.sh

db-up: ## Start the development PostgreSQL service.
	$(COMPOSE) -f docker-compose.dev.yml up -d

db-down: ## Stop the development PostgreSQL service without deleting data.
	$(COMPOSE) -f docker-compose.dev.yml down

docker-detect: ## Print hardware-based gunicorn/Track tuning recommendations.
	bash ops/docker/detect-hardware.sh

docker-dev-up: ## Start the full development stack in Docker (app + postgres).
	@test -f .env.docker.dev || (echo "Copy .env.docker.dev.example to .env.docker.dev first." >&2; exit 1)
	$(COMPOSE) -f docker-compose.dev-stack.yml --env-file .env.docker.dev up -d --build

docker-dev-down: ## Stop the Docker development stack.
	$(COMPOSE) -f docker-compose.dev-stack.yml --env-file .env.docker.dev down

migrate: ## Apply Django migrations.
	$(PYTHON) manage.py migrate

check: ## Run Django system checks and frontend type checking.
	$(PYTHON) manage.py check
	$(NPM) run typecheck --prefix frontend

test: test-backend test-frontend ## Run backend and frontend tests.

test-backend: ## Run the Django test suite.
	$(PYTHON) manage.py test

test-frontend: ## Run the frontend unit tests once.
	$(NPM) test --prefix frontend -- --run

build: ## Build the frontend production bundle.
	$(NPM) run build --prefix frontend

check-git: ## Reject tracked or staged runtime data and secrets.
	scripts/dev/setup.sh --check-git

