.PHONY: setup db dev-api dev-web dev test lint typecheck fmt check down

setup:
	cd backend && uv sync
	cd frontend && npm ci

db:
	docker compose up -d --wait db

dev-api:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-web:
	cd frontend && npm run dev

dev:
	$(MAKE) -j2 dev-api dev-web

test:
	cd backend && uv run pytest -q
	cd frontend && npm test

lint:
	cd backend && uv run ruff check && uv run ruff format --check
	cd frontend && npm run lint

typecheck:
	cd backend && uv run mypy
	cd frontend && npm run typecheck

fmt:
	cd backend && uv run ruff check --fix && uv run ruff format

check:
	./scripts/check.sh

down:
	docker compose down
