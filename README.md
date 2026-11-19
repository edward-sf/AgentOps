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

`make check` runs every check CI runs (it needs `make db`).

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