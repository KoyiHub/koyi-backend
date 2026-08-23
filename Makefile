.DEFAULT_GOAL := help
.PHONY: help setup install run shell migrate migrations superuser \
        test test-cov lint format typecheck check clean \
        worker beat flower schema services services-down secret reset-db

UV := uv
RUN := $(UV) run
MANAGE := $(RUN) python manage.py

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Setup -------------------------------------------------------------------

setup: ## First-time setup: venv, deps, .env, migrations, git hooks
	$(UV) sync
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	$(MANAGE) migrate
	$(RUN) pre-commit install --install-hooks
	$(RUN) pre-commit install --hook-type commit-msg
	@echo "\nSetup complete. Run 'make run' to start the dev server."

install: ## Sync dependencies from uv.lock
	$(UV) sync

secret: ## Print a fresh Django SECRET_KEY
	@$(RUN) python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"

# --- Running -----------------------------------------------------------------

run: ## Start the dev server (auto-reloading, on :8000)
	$(MANAGE) runserver_plus 0.0.0.0:8000

shell: ## Django shell with autoreload + model autoloading
	$(MANAGE) shell_plus --ipython

worker: ## Start a Celery worker
	$(RUN) celery -A config worker --loglevel=INFO --concurrency=2

beat: ## Start the Celery beat scheduler
	$(RUN) celery -A config beat --loglevel=INFO

services: ## Start Redis in Docker (only needed for real Celery runs)
	docker compose up -d

services-down: ## Stop the Docker services
	docker compose down

# --- Database ----------------------------------------------------------------

migrations: ## Generate migrations for model changes
	$(MANAGE) makemigrations

migrate: ## Apply migrations
	$(MANAGE) migrate

superuser: ## Create an admin user
	$(MANAGE) createsuperuser

reset-db: ## DESTRUCTIVE: delete the SQLite DB and re-migrate
	rm -f db.sqlite3
	$(MANAGE) migrate

# --- Quality -----------------------------------------------------------------

test: ## Run the test suite (parallel)
	$(RUN) pytest -n auto

test-cov: ## Run tests with a coverage report
	$(RUN) pytest --cov --cov-report=term-missing --cov-report=html

lint: ## Lint with ruff
	$(RUN) ruff check .
	$(RUN) ruff format --check .

format: ## Auto-format and auto-fix
	$(RUN) ruff check --fix .
	$(RUN) ruff format .

typecheck: ## Static type check with mypy
	$(RUN) mypy apps config

check: lint typecheck ## Everything CI runs, minus the tests
	$(MANAGE) check --deploy --settings=config.settings.production --fail-level WARNING || true
	$(MANAGE) makemigrations --check --dry-run

schema: ## Write the OpenAPI schema to schema.yml
	$(MANAGE) spectacular --color --file schema.yml

# --- Housekeeping ------------------------------------------------------------

clean: ## Remove caches and build artefacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml
