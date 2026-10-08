.PHONY: install dev test lint format typecheck arch check migrate up down

install:
	uv venv && uv pip install -e ".[dev]"

dev:
	.venv/bin/uvicorn gsoi_assistant.api.main:create_app --factory --reload

test:
	.venv/bin/pytest

lint:
	.venv/bin/ruff check . && .venv/bin/ruff format --check .

format:
	.venv/bin/ruff format . && .venv/bin/ruff check --fix .

typecheck:
	.venv/bin/mypy

arch:
	.venv/bin/lint-imports

check: lint typecheck arch test

migrate:
	.venv/bin/alembic upgrade head

up:
	docker compose up --build

down:
	docker compose down
