# M0 — Architecture and Project Setup: Design

- **Date:** 2026-11-17
- **Milestone:** [M0 - Architecture and project setup](https://github.com/edward-sf/AgentOps/milestone/1) (due 2026-11-20)
- **Status:** Draft, awaiting author approval

## 1. Intent

AgentOps is a solo-developer portfolio project: an evaluation and observability platform for AI agents (see `BRIEF.md`). Its centrepiece demo, in M5, is detecting a regression after a model or prompt change. M0 lays the foundation that M1–M7 build on: a running backend and frontend, a migrated PostgreSQL schema for the core entities, a local dev environment, CI, and a written development workflow that AI agents can follow on their own.

M0 is about **foundations that later milestones don't have to revisit**. Each later milestone should start by adding a feature, not by reworking setup.

## 2. Goals, non-goals, anti-goals

**Goals (milestone exit criteria)**
- The application starts locally (API + web), both natively and fully in Docker.
- The API connects to PostgreSQL, and a health endpoint proves it.
- Agent, AgentVersion, Dataset, and TestCase records can be created and read through the API.
- All nine core entities exist as tables via Alembic migrations.
- The API (OpenAPI) and data model are documented.
- A `ci` GitHub Actions workflow gates issue PRs and milestone PRs.
- The `AGENTS.md` and `CONTRIBUTING.md` placeholders are completed, including the agent workflow.
- The deterministic steps of the workflow are scripted (the `flow` CLI), and the agent roles have project skills (section 11).

**Non-goals (later milestones)**
- No LLM calls, LangGraph, or agent runtime (M1).
- No OpenTelemetry instrumentation (M1).
- No API endpoints for EvaluationRun, Trace, Span, EvaluationResult, or Metric (M1–M3). Their tables exist; their APIs don't.
- No dashboard or UI beyond a scaffold page (M4).
- No auth or multi-tenancy (not planned; single-user demo).
- No AWS or production deployment (M7).

**Anti-goals (would make M0 a failure even if it "works")**
- A dev loop that is slow or flaky for agents running tests repeatedly.
- CI that differs from local runs, so "green locally" doesn't predict "green in CI".
- Hand-maintained API types in the frontend that drift from the backend.
- Over-designing trace and metric tables to the point that M1 must fight them. They are a starting point, explicitly expected to evolve through new migrations.

## 3. Constraints

- Stack is fixed by the brief: Python and FastAPI, PostgreSQL (and pgvector later), Next.js and React, Docker, with AWS later.
- Branch model and issue lifecycle are fixed by `CONTRIBUTING.md`: issue branches are squash-merged into `milestone/m0`, and `milestone/m0` is merged into `main` with a merge commit by the author.
- Local machine: macOS (Apple Silicon), system Python 3.9 (not used), uv 0.13, Node 26, npm 11, Docker 29. pnpm is not installed.
- Due date 2026-11-20.

## 4. Approaches considered (local dev environment)

| Option | Summary | Trade-off |
| ------ | ------- | --------- |
| A. Everything in Compose | API, web, and DB all run in containers with bind mounts and hot reload | One command and identical environments, but slow file-watching on macOS, packages hidden from the IDE, and every test run goes through `docker compose exec`. |
| **B. Hybrid (chosen)** | Compose runs Postgres by default. API and web run natively (uv, npm). A `full` profile runs all three in containers. | Fast native loop that matches CI. Costs: two run modes that can diverge (mitigated by CI building the images), and a `DATABASE_URL` that differs between host and container. |
| C. Dev container | VS Code/Codespaces environment | Ties the project to one editor and adds a layer to maintain. Rejected. |

**Chosen: B.** The author agreed: for a solo-dev portfolio project, the agent-driven TDD loop and parity with CI outweigh one-command uniformity. Option A would be better with several contributors on different operating systems, or with dependencies that are hard to install natively. Neither applies here.

## 5. Architecture

### 5.1 Repository layout

Follows the structure in `README.md`. M0 creates only what it uses. Later modules (`agents/`, `evaluation/`, `observability/`) are created by the milestones that need them, not as empty stubs.

```
backend/
  pyproject.toml, uv.lock, alembic.ini, Dockerfile
  alembic/                 # env.py (async), versions/
  app/
    main.py                # app factory, router mounting, CORS
    config.py              # pydantic-settings
    db.py                  # async engine, session dependency
    api/                   # health.py, agents.py, datasets.py
    models/                # SQLAlchemy models (one module per aggregate)
    schemas/               # Pydantic request/response models
  tests/
frontend/
  package.json, package-lock.json, Dockerfile
  app/                     # Next.js App Router
  components/              # shadcn/ui components
  lib/api/                 # generated schema.ts + typed client
datasets/                  # sample seed data (JSON)
docs/                      # architecture.md (data model, ERD, API overview)
docker-compose.yml
Makefile
.github/workflows/ci.yml
scripts/flow/              # workflow CLI (uv project; section 11)
.claude/skills/            # orchestrate-milestone, review-issue-pr
```

### 5.2 Backend

- **Python 3.12**, managed by uv (`.python-version`, `pyproject.toml`, committed `uv.lock`).
- **FastAPI**, with routes under `/api/v1`. OpenAPI is served at `/openapi.json`, and the interactive docs at `/docs`.
- **SQLAlchemy 2.0 async** with the **psycopg 3** driver, and **Alembic** for migrations (async `env.py`, autogenerate reviewed by hand).
- **Pydantic v2** schemas, kept separate from ORM models (`Create`, `Update`, and `Read` variants).
- **Configuration** via `pydantic-settings`, read from env vars and `.env`.
- **Primary keys:** UUIDs generated by the database (`gen_random_uuid()`).
- **Timestamps:** `timestamptz`.
- **Flexible fields:** JSONB.

### 5.3 Frontend

- Next.js (App Router), TypeScript in strict mode, Tailwind CSS, and shadcn/ui initialised (as M4 recommends). Package manager: npm.
- API types are generated from the backend OpenAPI spec with `openapi-typescript` into `frontend/lib/api/schema.ts`, and called through `openapi-fetch`. The generated file is committed. `make api-types` regenerates it, and CI fails if it's stale.
- M0 UI: one home page that shows API health (DB connected yes/no) and lists agents and datasets from the API. It proves the frontend-to-backend connection, nothing more.
- Configuration: `NEXT_PUBLIC_API_URL`.

### 5.4 Database and local environment

- `docker-compose.yml`:
  - `db` service: image `pgvector/pgvector:pg17`, named volume, healthcheck, port 5432. The extension is not enabled until it's needed.
  - `full` profile: adds `api` (runs `alembic upgrade head`, then uvicorn) and `web` (Next.js production build).
  - Inside Compose, the API uses `db:5432`.
- Two databases: `agentops` (dev) and `agentops_test` (tests), both created by an init script in the `db` container.
- `Makefile` targets (names are final; the recipes are the builder's): `setup`, `db`, `migrate`, `seed`, `dev-api`, `dev-web`, `dev` (both), `test`, `lint`, `typecheck`, `fmt`, `api-types`, `up` (full profile), `down`.
- `make seed` loads `datasets/sample.json` (one agent with one version, and one dataset with ~3 test cases in the M2 example format) through the API layer's services. It is idempotent.

### 5.5 Configuration (`.env.example`)

`DATABASE_URL`, `TEST_DATABASE_URL`, `CORS_ORIGINS`, `NEXT_PUBLIC_API_URL`, plus the existing `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` (unused in M0). Defaults target the native (host) setup. Compose overrides `DATABASE_URL` for the `full` profile.

## 6. Data model

All nine entities get tables in M0. Fields marked *(later)* are present but unused until their milestone. M1–M3 may change trace, span, result, and metric tables through new migrations. The shapes below are a starting point, not a contract.

| Entity | Key fields | Notes |
| ------ | ---------- | ----- |
| **Agent** | id, name (unique), description, created_at, updated_at | A registered agent. |
| **AgentVersion** | id, agent_id → Agent, version (int, unique per agent, assigned server-side), label, provider (`anthropic` \| `openai`), model, system_prompt, parameters (JSONB: temperature, max_tokens, …), tools (JSONB list of tool names), created_at | **Immutable once created**: no update endpoint. A change means a new version. This is what makes the M5 comparisons trustworthy. |
| **Dataset** | id, name (unique), description, created_at, updated_at | A collection of evaluation cases. |
| **TestCase** | id, dataset_id → Dataset, key (e.g. `case_001`, unique per dataset), input (text), expected_behavior (JSONB list of strings), expected_output (text, nullable), tags (JSONB list), created_at, updated_at | Matches the M2 example case format. |
| **EvaluationRun** | id, agent_version_id, dataset_id, status (`pending` \| `running` \| `completed` \| `failed` \| `cancelled`), error, started_at, completed_at, created_at | *(M2)* |
| **Trace** | id, otel_trace_id (unique), agent_version_id, evaluation_run_id (nullable), test_case_id (nullable), status, input, output, error, started_at, ended_at | *(M1)* Nullable run and case so that traces can exist outside evaluations (M6 monitoring). |
| **Span** | id, trace_id → Trace, otel_span_id, parent_span_id (self, nullable), name, kind (`llm` \| `tool` \| `chain` \| `other`), status, attributes/input/output (JSONB), error, started_at, ended_at | *(M1)* One row per step or tool call. |
| **EvaluationResult** | id, evaluation_run_id, test_case_id, trace_id (nullable), passed, score (nullable), scores (JSONB by dimension), explanation, created_at | *(M3)* Unique (run, test_case). |
| **Metric** | id, trace_id, span_id (nullable), name (`latency_ms`, `input_tokens`, `output_tokens`, `cost_usd`, …), value (numeric), unit, recorded_at | *(M6)* A narrow table, so new metrics need no migration. |

**Deletion (my call: history is never silently destroyed):**
- Foreign keys from historical records use `ON DELETE RESTRICT`. That covers EvaluationRun, Trace, and EvaluationResult, whether they point to AgentVersion, TestCase, Dataset, or one another.
- Deleting an Agent or a Dataset is refused with `409` if any of its versions or test cases is referenced by a historical record. Otherwise it cascades to its versions or test cases (`ON DELETE CASCADE` from Agent to AgentVersion and from Dataset to TestCase).
- `DELETE …/test-cases/{case_id}` is refused with `409` if any Trace or EvaluationResult references the case.
- AgentVersions cannot be deleted on their own in M0.
- Span and Metric rows cascade from their Trace, and Metric rows also from their Span. A Trace itself is protected by the rules above.

**Enums:** stored as `text` with `CHECK` constraints, not native Postgres enums, so adding a value is a simple migration.

## 7. API contract (M0)

Code-first: the FastAPI-generated OpenAPI spec is the source of truth. All routes are under `/api/v1`, except health.

| Method and path | Purpose |
| --------------- | ------- |
| `GET /health` | `{status, database: "ok" \| "error"}`. Returns 200 if the DB is reachable, 503 otherwise. |
| `GET/POST /api/v1/agents`, `GET/PATCH/DELETE /api/v1/agents/{id}` | Agent CRUD |
| `GET/POST /api/v1/agents/{id}/versions`, `GET /api/v1/agents/{id}/versions/{version}` | Versions: create, list, and get only |
| `GET/POST /api/v1/datasets`, `GET/PATCH/DELETE /api/v1/datasets/{id}` | Dataset CRUD |
| `GET/POST /api/v1/datasets/{id}/test-cases`, `POST …/test-cases:bulk`, `GET/PATCH/DELETE …/test-cases/{case_id}` | Test cases. Bulk create takes a list and is all-or-nothing. |

**Conventions:**
- List endpoints take `limit` (default 50, max 200) and `offset`, and return `{items, total, limit, offset}`.
- Errors use FastAPI's `{"detail": …}` shape: `404` when not found, `409` on uniqueness or referential conflicts, `422` on validation errors.
- Responses use the Pydantic `Read` schemas.

## 8. Testing and quality

- **Backend:**
  - Tools: pytest, pytest-asyncio, and httpx `AsyncClient`, running against **real Postgres** (`agentops_test`). Tests don't use SQLite or mocks for the DB.
  - Migrations are applied once per session. Each test runs in a transaction that is rolled back afterwards.
  - A migration round-trip test runs `upgrade head`, then `downgrade base`, then `upgrade head` again.
  - Lint and types: ruff (lint and format), and mypy in strict mode on `app/`.
- **Frontend:** eslint, `tsc --noEmit`, vitest with Testing Library (a smoke test of the home page with a mocked client), and `next build`.
- **TDD:** issue work follows `CONTRIBUTING.md` (failing test first).

## 9. CI (`.github/workflows/ci.yml`)

The workflow is named `ci`. It runs on `pull_request` to `main` and `milestone/**`, and on `push` to those branches.

| Job | What it runs |
| --- | ------------ |
| `backend` | uv sync, then ruff check, ruff format --check, mypy, pytest (Postgres service container, `pgvector/pgvector:pg17`) |
| `frontend` | npm ci, then eslint, tsc, vitest, next build |
| `contracts` | Exports OpenAPI from the app and regenerates `schema.ts`. Fails on any diff. |
| `docker` | Builds both Dockerfiles (no push) |

The gate is "every job in `ci` green on the PR's head commit". Requiring it in branch protection is the author's call. The workflow gives it a stable name so it can be required.

## 10. Agent workflow (for `CONTRIBUTING.md` and `AGENTS.md`)

**Roles**
- **Orchestrator:** the root agent session. It owns the milestone: plans, dispatches work, watches CI, makes sure a review happens, posts the verdict, and merges.
- **Implementer:** a subagent the Orchestrator dispatches, one issue each. It branches, works with TDD, pushes, and opens the issue PR.
- **Reviewer:** a fresh subagent with no implementation context. The Orchestrator dispatches one for every issue PR, whoever implemented it. The Reviewer returns its verdict, the reviewed SHA and a findings file; the Orchestrator posts the verdict. Reviewers never post, merge or label.

**Records required before an agent squash-merges an issue PR into `milestone/m<N>`**
1. **Gate record:** the `ci` workflow is green for the PR's current head commit.
2. **Review record:** a PR comment, posted after the gate is green, that is scoped to the issue's plan section and acceptance criteria. Its first line is machine-checkable:

   ```
   Review verdict: PASS @ <full head SHA>
   ```

   (or `FAIL`), followed by findings.

**Rules**
- A verdict counts only if its SHA equals the PR's current head. Any new push invalidates it and needs a new gate and review.
- `FAIL` blocks the merge. The Implementer, or the Orchestrator, fixes and pushes, CI reruns, and the review is repeated.
- Minor findings that are deferred get backlog issues, per the existing rules.

## 11. Workflow automation

**Intent:** agents spend tokens only on judgement. That covers writing code, judging reviews, and writing titles, findings and summaries. Every deterministic step of the loop is a script, which also *enforces* the rules in section 10 rather than relying on agents to remember them (author request, 2026-11-17).

### 11.1 `flow` CLI

- A small Python package in `scripts/flow/` (its own `pyproject.toml`), run from the repo root as `./flow <command>`. That is a shim calling `uv run --project scripts/flow flow`, which keeps the backend's lockfile separate.
- It calls the `gh` CLI for all GitHub access (REST, plus GraphQL for the project board), so it uses the developer's existing `gh` auth and needs no token setup.
- Output is compact and machine-friendly. Exit code 0 means success or pass. A non-zero code comes with a short reason.

| Command | Does |
| ------- | ---- |
| `milestone-start <N>` | Reads `M<N>.T<n>` task headings from the milestone plan in `docs/plans/`. Creates or updates the matching issues (title, plan-section link, acceptance criteria, labels including `checkpoint`); re-running is safe. Adds them to the GitHub milestone and the project board, sets status `Ready`, and assigns the author. Creates and pushes `milestone/m<N>` if it's missing and opens the draft milestone PR. |
| `status [N]` | One compact table per issue: board status, branch, PR, CI result on head, verdict on head. |
| `issue-start <n>` | Fetches, branches `<n>-<slug>` from the up-to-date milestone branch, and sets the board status to `In progress`. Refuses if the issue isn't `Ready` or is blocked by an unapproved checkpoint. |
| `issue-pr <n> "<conventional title>" [--notes FILE]` | Pushes and opens the PR into the milestone branch, with a body from the template, `Part of #n`, and deviation notes. Sets the board to `In review`. |
| `pr-gate <pr>` | Blocks until `ci` finishes on the head commit. Exits 0 if green. If red, prints only the failing job and step and the tail of its log. |
| `review-context <pr>` | Prints one bundle: the issue's plan section, its acceptance criteria, the head SHA, and the PR diff. |
| `post-verdict <pr> PASS\|FAIL <findings-file> --sha <sha>` | Posts the review comment with the `Review verdict: … @ <sha>` header. Takes the reviewed SHA (from `review-context`) and refuses if the PR head has moved. |
| `issue-merge <pr>` | **Guard and merge.** It refuses unless `ci` is green on head, a `PASS @ <head SHA>` verdict exists, and no unapproved checkpoint blocks the issue. Otherwise it squash-merges with the PR title as the message, deletes the branch, and sets the board to `Merged to milestone`. |
| `milestone-pr <N>` | Fills the milestone PR's "Issue PRs" list and `Closes #…` lines from merged PRs. Agents write the summary, verification, and deviations sections. |

**Checkpoint approval (my call, revised during planning):** a checkpoint counts as approved when its issue carries the `checkpoint-approved` label, which only the author applies. `issue-merge` and `issue-start` check this. A GitHub review approval isn't possible here, because agents open PRs under the author's own account, and GitHub doesn't let authors approve their own PRs. Nothing technically stops an agent from applying the label, so `AGENTS.md` forbids it.

**Testing:** the plan parsing, verdict parsing, and merge-guard logic are pure functions with pytest unit tests. The `gh` calls sit behind one thin module that tests replace with a fake. `flow` runs in the `ci` workflow's `backend` job, or its own job; the builder decides.

**Plan format contract:** the plan must have one heading per task in the form `### M<N>.T<n> <title>`, each with an `**Acceptance criteria**` list, an optional `**Checkpoint**` marker, and an optional `**Depends on:** M<N>.T<n>, …` line. `issue-start` refuses to start an issue until its dependencies are merged into the milestone branch, and approved if they are checkpoints. `milestone-start` and `review-context` depend on this format, so the plan template documents it.

**Local checks:** `make check` runs every lint, type, and test step quietly and prints only failures. This is the same set of checks as `ci`.

### 11.2 Skills and agent instructions

The work is split by role, not by script. A per-script skill would repeat `--help`, and every skill description costs context on every turn.

- **`AGENTS.md`:** a short command reference (one line per `flow` command and `make check`). It is always loaded and works with any agent tool. It is all an Implementer needs (`issue-start` → TDD → `make check` → `issue-pr`).
- **`.claude/skills/orchestrate-milestone/`:** the Orchestrator's loop. Run `milestone-start`. Dispatch Implementers, and say what their prompt must contain. Wait with `pr-gate`, then dispatch a fresh Reviewer for every issue PR and post its verdict. Pause at checkpoints and notify the author. Merge with `issue-merge`. Finish with `milestone-pr`. It covers only what's specific to this repo, and defers the general pattern of dispatching subagents to superpowers' `subagent-driven-development`.
- **`.claude/skills/review-issue-pr/`:** the Reviewer's procedure (the Orchestrator has already gated the PR). Run `review-context`, judge against the plan section and acceptance criteria only, use the FAIL vs deferred-minor rules (a deferred minor finding gets a backlog issue), and return the verdict, reviewed SHA and findings file to the Orchestrator, which posts it with `post-verdict`. This is where the review standard lives, so fresh Reviewers apply it consistently.
- Precedence when sources disagree: `./flow` > CONTRIBUTING.md > skills and AGENTS.md.

### 11.3 Bootstrapping within M0

`flow` doesn't exist when M0 starts, so M0's own start steps are done by hand once: the branch, the draft PR, the issues, and the board. The task order is:
1. CI and the minimal skeletons.
2. The `flow` CLI.
3. Everything else.

From the third task on, every M0 issue goes through `issue-start`, `issue-pr`, `pr-gate`, `post-verdict`, and `issue-merge`. The first two tasks are merged by hand, following the same rules.

## 12. Repository housekeeping in M0

- Create labels: `type:feature`, `type:bug`, `type:docs`, `type:chore`, and `checkpoint`.
- Make sure the project board's Status field has the options `Future`, `Ready`, `In progress`, `In review`, `Merged to milestone`, and `Done`.
- Push `milestone/m0`, open the draft milestone PR, create M0 issues from the plan (`M0.T<n> …`), and add them to the board and the milestone.
- Commit the pending doc edits (`README.md`, `.gitignore`, `AGENTS.md`, `CONTRIBUTING.md`, `.github/` templates) directly to `milestone/m0` as part of starting the milestone, before any issue branch is cut. Only `main` forbids direct pushes.
- **Bootstrapping the gate (my call):** the first task adds the `ci` workflow, together with the minimal backend and frontend skeletons it checks. A `pull_request` run uses the workflow file from the PR itself, so that PR produces its own gate record. No task needs an exception to the merge rule.
- Specs live in `docs/specs/` and plans in `docs/plans/`, both committed (author decision, 2026-11-17; the `docs/superpowers/*` ignore rule is removed). Issues link to plan sections, and the Reviewer reads them from the repo.
- **Checkpoint (author decision):** the task that defines the data model and the first migration is labelled `checkpoint`. Tasks that depend on it wait for the author's approval. No other M0 task is a checkpoint; the milestone PR review covers the rest.
- Add `!frontend/lib/` to `.gitignore`, because the Python template's `lib/` rule would otherwise drop the generated `frontend/lib/api/schema.ts`.
- Add `docs/architecture.md`, with a Mermaid ERD of section 6 and an overview of the API in section 7. It points to `/docs` for the full contract.

## 13. Left to the builder

- Exact dependency versions (latest stable at implementation time).
- Recipes for each Makefile target, and Dockerfile details (multi-stage, slim base, non-root user).
- Internal module structure inside `app/` beyond section 5.1, such as whether to add a service layer between routes and the ORM.
- Exact index choices beyond the uniques and FKs stated above.
- Wording of the `AGENTS.md`, `CONTRIBUTING.md`, and skill text, within the rules of sections 10–11.
- The `flow` CLI framework (e.g. argparse or Typer), its internal structure, and output formatting.
- How plan tasks split into issues (the plan decides this; the issue IDs follow).
