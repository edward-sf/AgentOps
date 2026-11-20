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
make dev               # run the API, the web app and the evaluation worker
```

Then open http://localhost:3000.

To run everything in Docker instead, use `make up` (and `make down` to stop).

### The evaluation worker

`POST /api/v1/evaluation-runs` only queues a `pending` run. The worker (`python -m app.worker`) claims runs from the `evaluation_runs` table one at a time and executes their cases. `make dev` starts it; `make dev-worker` runs it alone, restarting on code changes. In Docker it is the `worker` service of the `full` profile. More than one worker can run at once.

Each case it executes calls the model API, so it needs `ANTHROPIC_API_KEY` and costs money. Without the key, every run it claims ends `failed`. Its settings, all optional:

- `EVAL_CONCURRENCY` (default 4): how many cases of a run execute at once.
- `EVAL_STALE_AFTER_SECONDS` (default 60): a `running` run whose heartbeat is older than this is reclaimed by another worker.
- `EVAL_POLL_INTERVAL_SECONDS` (default 1): how long an idle worker waits before polling again.

On SIGTERM or Ctrl-C it stops claiming, lets the cases in flight finish, and exits. The run it was executing stays `running` and is resumed after `EVAL_STALE_AFTER_SECONDS`, skipping the cases that already have results.

`make check` runs every check CI runs (it needs `make db`). No check calls a model API or needs `ANTHROPIC_API_KEY`.

### Agent runs and the API key

Three targets call the real Anthropic API, cost money and need `ANTHROPIC_API_KEY`. None runs in `make check` or CI.

- `make smoke`: runs the seeded `weather-bot` v1 on "Find the weather in Seattle" through `POST /api/v1/runs` (two model calls) and prints the stored span tree. It needs `make db migrate seed`. It exits 1 if the run doesn't complete with a tool span, and 2 without the key.
- `make eval` (author only): submits the seeded `weather-basics` dataset (26 cases) against `weather-bot` v1 through `POST /api/v1/evaluation-runs`, executes the run in process with the worker loop, and prints one line per result (case key, status, trace id, and the first 80 characters of the answer or error), then the counts. Each case makes one or more model calls, so a run costs roughly 50–80 model calls. It needs `make db migrate seed`. It never executes a run it didn't create: if any other evaluation run is `pending` or `running`, it lists them and exits 1 without submitting (cancel them with `POST /api/v1/evaluation-runs/{id}/cancel`, or stop the other worker). It exits 0 if the run ends `completed` with no `error` result, 1 otherwise, and 2 without the key (before anything is submitted).
- `make record` (author only): re-records the Anthropic adapter fixtures in `backend/tests/agents/fixtures/anthropic/`.

Use a dedicated key with a spend limit. Keep it out of your shell history: store it once in the macOS Keychain (the command prompts for the key), then read it from there for each run.

```
security add-generic-password -a "$USER" -s anthropic-api-key -w
ANTHROPIC_API_KEY="$(security find-generic-password -s anthropic-api-key -w)" make smoke
ANTHROPIC_API_KEY="$(security find-generic-password -s anthropic-api-key -w)" make eval     # author only
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