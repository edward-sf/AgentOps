.PHONY: api-types setup db migrate seed dev-api dev-web dev test lint typecheck fmt check down

setup:
	cd backend && uv sync
	cd frontend && npm ci

db:
	docker compose up -d --wait db

migrate:
	cd backend && uv run alembic upgrade head

seed:
	cd backend && uv run python -m app.seed ../datasets/sample.json

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

api-types:
	cd backend && uv run python -m app.export_openapi > ../frontend/lib/api/openapi.json
	cd frontend && npx openapi-typescript lib/api/openapi.json -o lib/api/schema.ts
