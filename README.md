# AgentOps
> Autonomous AI Agent Evaluation & Monitoring Platform

An observability and evaluation platform that allows engineering teams to test, monitor, and debug AI agents before and after deployment.

## Documentation

- [Architecture](docs/architecture.md): system overview, data model (ERD), deletion rules and API.

## Repo Structure

The codebase is designed around clear functional boundaries.

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