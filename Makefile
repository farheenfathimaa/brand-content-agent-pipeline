PYTHON ?= python3
VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff

.PHONY: help venv install dev-install run demo eval test lint docker-build docker-up docker-down clean

help:
	@echo "Targets:"
	@echo "  make install      Create .venv and install runtime dependencies"
	@echo "  make dev-install  Also install test, lint, and Playwright dependencies"
	@echo "  make run          Start the API at http://localhost:8000"
	@echo "  make demo         Run the zero-key end-to-end demo"
	@echo "  make eval         Run the 20-brief mock eval and write reports/"
	@echo "  make test         Run the pytest suite"
	@echo "  make lint         Run ruff"
	@echo "  make docker-build Build the container image"
	@echo "  make docker-up    Start the container with Docker Compose"
	@echo "  make docker-down  Stop the Docker Compose stack"
	@echo "  make clean        Remove the local virtualenv and caches"

venv: $(PY)

$(PY):
	$(PYTHON) -m venv $(VENV)

install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

dev-install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

run: install
	$(UVICORN) app.main:app --host 0.0.0.0 --port 8000 --reload

demo: install
	$(PY) -m app.demo

eval: install
	$(PY) -m app.evals.runner --output-dir reports

test: dev-install
	$(PYTEST)

lint: dev-install
	$(RUFF) check .

docker-build:
	docker build -t brand-content-agent-pipeline:local .

docker-up:
	docker compose up --build

docker-down:
	docker compose down

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
