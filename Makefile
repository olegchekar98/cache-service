.PHONY: install test lint format typecheck check run docker

install:
	pip install -e ".[dev]"

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
	uvicorn cache_service.main:app --reload

docker:
	docker compose up --build
