.PHONY: install lock test lint format typecheck check run docker

install:
	pip install -r requirements-dev.lock
	pip install --no-deps -e .

# Regenerate the pinned dependency sets after changing pyproject.toml (needs pip-tools).
lock:
	pip-compile -q --strip-extras --extra postgres --no-emit-index-url -o requirements.lock pyproject.toml
	pip-compile -q --strip-extras --extra postgres --extra dev --no-emit-index-url -o requirements-dev.lock pyproject.toml

test:
	pytest --cov --cov-report=term-missing

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .
	ruff check --fix .

typecheck:
	mypy

check: lint typecheck test

run:
	uvicorn --factory cache_service.main:create_app --reload

docker:
	docker compose up --build
