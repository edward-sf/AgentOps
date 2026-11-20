.PHONY: api-types setup db migrate seed dev-api dev-web dev-worker dev test lint typecheck fmt check up down record smoke eval

setup:
	cd backend && uv sync
	cd frontend && npm ci

db:
	docker compose up -d --wait db

migrate:
	cd backend && uv run alembic upgrade head

seed:
	cd backend && uv run python -m app.seed ../datasets/sample.json

# Author only: calls the real Anthropic API (costs money, needs ANTHROPIC_API_KEY).
record:
	cd backend && uv run python -m app.agents.providers.record

# Manual: one real agent run (2 model calls) of the seeded weather-bot; needs ANTHROPIC_API_KEY.
smoke:
	cd backend && uv run python -m app.smoke

# Author only: one real evaluation run of weather-basics (26 cases, about 50-80 model calls)
# against weather-bot v1; needs ANTHROPIC_API_KEY.
eval:
	cd backend && uv run python -m app.eval

dev-api:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-web:
	cd frontend && npm run dev

# The evaluation worker, restarted on code changes. It needs ANTHROPIC_API_KEY to run cases.
dev-worker:
	cd backend && uv run watchfiles --filter python --sigint-timeout 60 app.worker.main app

dev:
	$(MAKE) -j3 dev-api dev-web dev-worker

test:
	cd backend && uv run pytest -q
	cd frontend && npm test
	cd scripts/flow && uv run pytest -q

lint:
	cd backend && uv run ruff check && uv run ruff format --check
	cd frontend && npm run lint
	cd scripts/flow && uv run ruff check && uv run ruff format --check

typecheck:
	cd backend && uv run mypy
	cd frontend && npm run typecheck
	cd scripts/flow && uv run mypy

fmt:
	cd backend && uv run ruff check --fix && uv run ruff format
	cd scripts/flow && uv run ruff check --fix && uv run ruff format
	cd frontend && npm run lint -- --fix

check:
	./scripts/check.sh

up:
	docker compose --profile full up -d --build --wait

down:
	docker compose --profile full down

api-types:
	cd backend && uv run python -m app.export_openapi > ../frontend/lib/api/openapi.json
	cd frontend && npx openapi-typescript lib/api/openapi.json -o lib/api/schema.ts
