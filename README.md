# AgentOps
> Autonomous AI Agent Evaluation & Monitoring Platform

An observability and evaluation platform that allows engineering teams to test, monitor, and debug AI agents before and after deployment.

## Documentation

- [Architecture](docs/architecture.md): system overview, data model (ERD), deletion rules and API.

## Getting started

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Node 22+, and the `gh` CLI (for `./flow`).

```
cp .env.example .env   # optional; the defaults work locally
make setup             # install backend and frontend dependencies
make db                # start Postgres
make migrate           # apply migrations
make seed              # load sample data
make dev               # run the API and the web app
```

Then open http://localhost:3000.

To run everything in Docker instead, use `make up` (and `make down` to stop).

`make check` runs every check CI runs (it needs `make db`). No check calls a model API or needs `ANTHROPIC_API_KEY`.

### Agent runs and the API key

Two targets call the real Anthropic API, cost money and need `ANTHROPIC_API_KEY`. Neither runs in `make check` or CI.

- `make smoke`: runs the seeded `weather-bot` v1 on "Find the weather in Seattle" through `POST /api/v1/runs` (two model calls) and prints the stored span tree. It needs `make db migrate seed`. It exits 1 if the run doesn't complete with a tool span, and 2 without the key.
- `make record` (author only): re-records the Anthropic adapter fixtures in `backend/tests/agents/fixtures/anthropic/`.

Use a dedicated key with a spend limit. Keep it out of your shell history: store it once in the macOS Keychain (the command prompts for the key), then read it from there for each run.

```
security add-generic-password -a "$USER" -s anthropic-api-key -w
ANTHROPIC_API_KEY="$(security find-generic-password -s anthropic-api-key -w)" make smoke
ANTHROPIC_API_KEY="$(security find-generic-password -s anthropic-api-key -w)" make record   # author only
```

Alternatively, put `ANTHROPIC_API_KEY=...` in `.env` (gitignored) and `chmod 600 .env`.

**Databases seeded before M1:** the seeded `weather-bot` v1 used to have `temperature: 0`, which M1 no longer accepts. Seeding is idempotent and versions are immutable, so re-running `make seed` doesn't fix it, and a run returns `422` naming `temperature`. Reset the dev database volume and reseed:

```
docker compose --profile full down -v   # deletes all local data
make db migrate seed
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md) and [docs/architecture.md](docs/architecture.md) for more.

## Repo Structure

Target layout (built up across milestones). The codebase is designed around clear functional boundaries.

```
agentops/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── agents.py
│   │   │   ├── datasets.py
│   │   │   ├── evaluations.py
│   │   │   ├── traces.py
│   │   │   └── analytics.py
│   │   ├── agents/
│   │   │   ├── runtime.py
│   │   │   ├── graph.py
│   │   │   └── tools.py
│   │   ├── evaluation/
│   │   │   ├── runner.py
│   │   │   ├── evaluators.py
│   │   │   └── regression.py
│   │   ├── observability/
│   │   │   ├── tracing.py
│   │   │   └── metrics.py
│   │   ├── models/
│   │   ├── schemas/
│   │   └── main.py
│   ├── tests/
│   └── Dockerfile
├── frontend/
│   ├── app/
│   ├── components/
│   ├── features/
│   └── Dockerfile
├── datasets/
├── infrastructure/
├── docker-compose.yml
├── BRIEF.md
├── CONTRIBUTING.md
└── README.md
```