# M0 — Architecture and Project Setup: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Follow `CONTRIBUTING.md` for branches, PRs, and records.

**Goal:** Running FastAPI and Next.js scaffolds on a migrated PostgreSQL schema, with Agent and Dataset APIs, a hybrid Docker dev environment, a `ci` gate, and the scripted agent workflow (`flow` CLI and skills).

**Architecture:** A monorepo with `backend/` (Python 3.12, uv, FastAPI, async SQLAlchemy 2.0, Alembic), `frontend/` (Next.js App Router, TypeScript, types generated from OpenAPI), and `scripts/flow/` (a Python CLI over `gh`). Compose runs Postgres by default, and a `full` profile adds containerised API and web. CI runs the same checks natively, against a Postgres service container.

**Tech Stack:** Python 3.12, uv, FastAPI, SQLAlchemy 2.0 (asyncio), psycopg 3, Alembic, Pydantic v2, pydantic-settings, pytest, pytest-asyncio, httpx, ruff, mypy; Next.js, React, TypeScript, Tailwind, shadcn/ui, openapi-typescript, openapi-fetch, vitest, Testing Library; Docker Compose, `pgvector/pgvector:pg17`; GitHub Actions; `gh` CLI.

**Spec:** [`docs/specs/2026-11-17-m0-architecture-design.md`](../specs/2026-11-17-m0-architecture-design.md)

## Global Constraints

- Python **3.12** via uv (`.python-version` = `3.12`). Use npm, not pnpm.
- Postgres image **`pgvector/pgvector:pg17`**. Do not enable the extension yet.
- Two databases: `agentops` (dev) and `agentops_test` (tests). Local credentials are `agentops`/`agentops`, on `localhost:5432` from the host and `db:5432` inside Compose.
- API routes are under **`/api/v1`**. The one exception is `GET /health`.
- Primary keys are UUIDs with the server default `gen_random_uuid()`. Timestamps are `timestamptz`, with `created_at` and `updated_at` set server-side. Flexible fields use JSONB.
- Enums are `text` columns with `CHECK` constraints, never native Postgres enums.
- Lists take `limit` (default **50**, min 1, max **200**) and `offset` (min 0), and return `{items, total, limit, offset}`.
- Errors use the shape `{"detail": ...}`: **404** not found, **409** uniqueness or referential conflict, **422** validation.
- AgentVersions are immutable. They have no update endpoint and can't be deleted on their own.
- Foreign keys from historical records (EvaluationRun, Trace, EvaluationResult, Metric) to their targets use `ON DELETE RESTRICT`. Agent→AgentVersion and Dataset→TestCase use `CASCADE`. Span and Metric rows cascade from their Trace.
- The verdict comment's first line is exactly `Review verdict: PASS @ <40-char head SHA>` (or `FAIL`).
- The GitHub workflow is named `ci`. Issue branches are named `<issue-number>-<short-slug>`.
- Tests run against real Postgres. Don't use SQLite or mock the database.

## Review Focus

1. **Two versions created at the same time for one agent:** both succeed, with distinct consecutive version numbers. Neither gets a 500. (Test in M0.T6.)
2. **A bulk test-case payload with duplicate keys,** within the payload or against existing cases: 409, and nothing is inserted. (Test in M0.T7.)
3. **Renaming an agent or dataset to a name that already exists:** 409, not 500. (Tests in M0.T6, M0.T7.)
4. **Pagination out of bounds** (`limit=0`, `limit=201`, `offset=-1`): 422. (Test in M0.T6.)
5. **A PASS verdict at an older SHA, or a FAIL posted after a PASS at the same SHA:** neither allows a merge. Only the latest verdict at the current head counts. (Tests in M0.T2.)

---

## Milestone start (Orchestrator, manual, once)

`flow` doesn't exist yet, so do these steps by hand. They are not an issue.

1. On `milestone/m0`, add `!frontend/lib/` after the `lib/` line in `.gitignore`. Commit the pending doc edits and these planning docs: `git add -A && git commit -m "docs: project docs, M0 design, roadmap and plan"`. Push with `git push -u origin milestone/m0`.
2. Create labels with `gh label create <name>`, for `type:feature`, `type:bug`, `type:docs`, `type:chore`, `checkpoint`, and `checkpoint-approved`.
3. Make sure the project 10 Status field has the options `Future`, `Ready`, `In progress`, `In review`, `Merged to milestone`, and `Done`. Adding missing ones needs the GitHub UI, so ask the author.
4. Create one issue per `### M0.T<n>` heading below. Use the title `M0.T<n> <title>`, and a body with the plan-section link, the goal, and the acceptance criteria. Add the labels, milestone 1, the assignee `edward-sf`, project 10, and status `Ready`.
5. Open the draft milestone PR: `gh pr create --draft --base main --head milestone/m0 --title "M0 — Architecture and project setup"`, using the body from `.github/PULL_REQUEST_TEMPLATE/milestone.md`.

M0.T1 and M0.T2 follow the issue lifecycle by hand, with the same gate and verdict format. From M0.T3 on, use `./flow`.

---

### M0.T1 CI and project skeletons

**Depends on:** none

**Goal:** Minimal backend and frontend that start, a Postgres service, `make check`, and the `ci` workflow that gates every later PR.

**Acceptance criteria**
- [ ] `make setup && make db && make dev` starts the API on :8000 and the web app on :3000.
- [ ] `GET /health` returns 200 `{"status":"ok","database":"ok"}` when Postgres is up, and 503 `{"status":"degraded","database":"error"}` when it isn't.
- [ ] `make check` runs every backend and frontend check. It prints one line per passing step and the full output only for failing steps, and exits non-zero on any failure.
- [ ] The `ci` workflow runs the `backend` and `frontend` jobs on PRs to `main` and `milestone/**` and passes on this PR.

**Files:**
- Create: `backend/pyproject.toml`, `backend/.python-version`, `backend/uv.lock`, `backend/app/__init__.py`, `backend/app/main.py`, `backend/app/config.py`, `backend/app/db.py`, `backend/app/api/__init__.py`, `backend/app/api/health.py`, `backend/tests/conftest.py`, `backend/tests/api/test_health.py`
- Create: `frontend/` (create-next-app output), `frontend/vitest.config.ts`, `frontend/vitest.setup.ts`, `frontend/app/__tests__/page.test.tsx`
- Create: `docker-compose.yml`, `infrastructure/postgres/init/01-create-test-db.sql`, `Makefile`, `scripts/check.sh`, `.github/workflows/ci.yml`
- Modify: `.env.example`, `.gitignore`

**Interfaces:**
- Produces:
  - `app.config.Settings(BaseSettings)`, with fields:
    - `database_url: str = "postgresql+psycopg://agentops:agentops@localhost:5432/agentops"`
    - `test_database_url: str = "postgresql+psycopg://agentops:agentops@localhost:5432/agentops_test"`
    - `cors_origins: list[str] = ["http://localhost:3000"]`
  - `app.config.get_settings() -> Settings`, `lru_cache`d.
  - `app.main.create_app(settings: Settings | None = None) -> FastAPI`. Its lifespan creates `app.state.engine` (`AsyncEngine`) and `app.state.sessionmaker` (`async_sessionmaker[AsyncSession]`, `expire_on_commit=False`), and disposes the engine on shutdown. It also creates the module-level `app = create_app()`.
  - `app.db.get_session(request: Request) -> AsyncIterator[AsyncSession]`: yields a session, commits on success, and rolls back on exception.
  - Test fixtures in `tests/conftest.py`:
    - `settings` (a `Settings` whose `database_url` is the test DB URL)
    - `app` (`create_app(settings)`, run with its lifespan)
    - `client` (`httpx.AsyncClient(transport=ASGITransport(app), base_url="http://test")`)
  - Makefile targets: `setup`, `db`, `dev-api`, `dev-web`, `dev`, `test`, `lint`, `typecheck`, `fmt`, `check`, `down`.

- [ ] **Step 1: Scaffold the backend.** Run `uv init --app backend`, pin 3.12, and add these dependencies:
  - runtime: `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `psycopg[binary]`, `alembic`, `pydantic-settings`
  - dev: `pytest`, `pytest-asyncio`, `httpx`, `ruff`, `mypy`

  In `pyproject.toml`, configure:
  - `[tool.pytest.ini_options]`: `asyncio_mode = "auto"`, `asyncio_default_fixture_loop_scope = "session"`, `asyncio_default_test_loop_scope = "session"`
  - `[tool.mypy]`: `strict = true`, `plugins = ["pydantic.mypy"]`, `files = ["app"]`
  - `[tool.ruff]`: `line-length = 100`, lint `select = ["E","F","I","UP","B","SIM","ASYNC"]`

- [ ] **Step 2: Add Compose and the test database.** `docker-compose.yml` has a `db` service:
  - image `pgvector/pgvector:pg17`
  - env `POSTGRES_USER/PASSWORD/DB = agentops`
  - port `5432:5432`
  - named volume `pgdata`
  - `./infrastructure/postgres/init` mounted at `/docker-entrypoint-initdb.d`
  - healthcheck `pg_isready -U agentops`

  The init SQL is `CREATE DATABASE agentops_test OWNER agentops;`. Run `docker compose up -d --wait db`, then `docker compose exec db psql -U agentops -lqt | cut -d'|' -f1`. Expected: both `agentops` and `agentops_test` are listed.

- [ ] **Step 3: Write the failing health tests** in `tests/api/test_health.py`:

```python
async def test_health_ok_when_database_reachable(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}

async def test_health_503_when_database_unreachable(settings):
    bad = settings.model_copy(update={"database_url": "postgresql+psycopg://x:x@localhost:1/none"})
    app = create_app(bad)
    async with LifespanManager(app), AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        r = await c.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "database": "error"}
```

  Add `asgi-lifespan` as a dev dependency for `LifespanManager`, and use it in the `app` fixture too.

- [ ] **Step 4: Run the tests to see them fail.** Run `cd backend && uv run pytest tests/api/test_health.py -v`. Expected: an ImportError or 404.

- [ ] **Step 5: Implement the code.** Write `config.py`, `db.py`, `main.py` (CORS from `settings.cors_origins`), and `api/health.py`. The health check runs `SELECT 1` through the engine, with a 2s timeout, and catches every exception to return 503. The engine is created lazily, so startup doesn't fail when the database is down.

- [ ] **Step 6: Run the tests to see them pass.** Run `uv run pytest -v`. Expected: 2 passed.

- [ ] **Step 7: Scaffold the frontend.** Run `npx create-next-app@latest frontend --ts --eslint --tailwind --app --no-src-dir --import-alias "@/*" --use-npm`, then `cd frontend && npx shadcn@latest init -d`. Set up vitest with jsdom, `@vitejs/plugin-react`, `@testing-library/react`, and `@testing-library/jest-dom`. Add the npm scripts:
  - `"typecheck": "tsc --noEmit"`
  - `"test": "vitest run"`

  `app/page.tsx` renders an `<h1>AgentOps</h1>`.

- [ ] **Step 8: Write the frontend smoke test** in `app/__tests__/page.test.tsx`. Render `<Page />` and assert `screen.getByRole("heading", { name: "AgentOps" })` is in the document. Run `npm test`. Expected: 1 passed.

- [ ] **Step 9: Add `scripts/check.sh` and the Makefile.**
  - `check.sh` runs these steps from the repo root, each with output captured:
    - backend: `ruff check`, `ruff format --check`, `mypy`, `pytest -q`
    - frontend: `npm run lint`, `npm run typecheck`, `npm test`
  - On success a step prints `✓ <step>`. On failure it prints `✗ <step>` followed by the captured output. The script runs all steps and exits 1 if any failed.
  - Later tasks append steps to the `STEPS` array.
  - `make check` calls the script. `make dev` runs `dev-api` (`uv run uvicorn app.main:app --reload --port 8000`) and `dev-web` (`npm run dev`) in parallel.
  - `make setup` runs `uv sync` and `npm ci`. `make db` runs `docker compose up -d --wait db`.

- [ ] **Step 10: Write `.github/workflows/ci.yml`.**
  - `name: ci`
  - Triggers: `pull_request` to `[main, "milestone/**"]` and `push` to the same branches.
  - Job `backend`:
    - `services.postgres`: image `pgvector/pgvector:pg17`, env `POSTGRES_USER/PASSWORD=agentops`, `POSTGRES_DB=agentops_test`, port 5432, health options
    - `astral-sh/setup-uv`, then `uv sync --locked`, ruff check, ruff format --check, mypy, pytest
    - env `DATABASE_URL` and `TEST_DATABASE_URL` both pointing to `agentops_test`
  - Job `frontend`: `actions/setup-node` (node 22, npm cache), then `npm ci`, lint, typecheck, test, build.
  - Both jobs set `defaults.run.working-directory`.

- [ ] **Step 11: Update `.env.example`.** Add `DATABASE_URL`, `TEST_DATABASE_URL`, `CORS_ORIGINS`, and `NEXT_PUBLIC_API_URL=http://localhost:8000`, keeping the existing API keys.

- [ ] **Step 12: Verify.** Run `make check`. Expected: every line starts with `✓` and the exit code is 0. Then run `make dev` and `curl -s localhost:8000/health`. Expected: `{"status":"ok","database":"ok"}`.

- [ ] **Step 13: Commit and open the PR.** Run `git commit -m "feat: add backend/frontend skeletons, compose db and ci workflow"`. The PR title is `feat: add project skeletons and ci workflow`, and the body says `Part of #<issue>`.

---

### M0.T2 `flow` CLI: issue loop

**Depends on:** M0.T1

**Goal:** Script the deterministic per-issue steps, and enforce the gate and verdict rules at merge time.

**Acceptance criteria**
- [ ] `./flow status`, `issue-start`, `issue-pr`, `pr-gate`, `review-context`, `post-verdict`, and `issue-merge` work against the live repo.
- [ ] `issue-merge` refuses, with a reason, when any of these is true:
  - CI is not green on head
  - there is no `PASS` verdict at the head SHA
  - the latest verdict at head is `FAIL`
  - the issue is a checkpoint without the `checkpoint-approved` label
  - the PR base is not a `milestone/` branch
- [ ] `issue-start` refuses when a dependency (`**Depends on:**`) isn't `Merged to milestone`, or is a checkpoint without approval.
- [ ] Unit tests cover plan parsing, verdict format and parsing, and the merge guard. A `flow` job runs in `ci`, and its steps are in `make check`.

**Files:**
- Create: `flow` (repo-root shim), `scripts/flow/pyproject.toml`, `scripts/flow/flow/__init__.py`, `config.py`, `gh.py`, `plan.py`, `verdict.py`, `guard.py`, `board.py`, `cli.py`, `scripts/flow/tests/test_plan.py`, `test_verdict.py`, `test_guard.py`, `scripts/flow/tests/fixtures/plan_sample.md`, `.github/PULL_REQUEST_TEMPLATE/issue.md`
- Modify: `.github/workflows/ci.yml`, `scripts/check.sh`

**Interfaces:**
- Produces:
  - The shim `flow`, which runs `exec uv run --project "$(dirname "$0")/scripts/flow" flow "$@"`. Agents call `./flow <command>`. This is a deviation from the spec's `uv run flow`; uv workspaces would merge the backend lockfile.
  - `flow.config`:
    - `OWNER = "edward-sf"`, `REPO = "AgentOps"`, `PROJECT_NUMBER = 10`, `AUTHOR = "edward-sf"`
    - `STATUSES = ("Future","Ready","In progress","In review","Merged to milestone","Done")`
    - `CHECKPOINT_LABEL = "checkpoint"`, `APPROVED_LABEL = "checkpoint-approved"`
  - `flow.gh`:
    - `run(args: list[str], *, input: str | None = None) -> str` raises `GhError(message)` on a non-zero exit.
    - `json(args: list[str]) -> Any`
    - `graphql(query: str, **variables: object) -> dict[str, Any]`
  - `flow.plan`:
    - `@dataclass(frozen=True) class Task(id: str, milestone: int, number: int, title: str, body: str, acceptance: list[str], checkpoint: bool, depends_on: list[str])`. `id` looks like `"M0.T3"`.
    - `parse_plan(text: str) -> list[Task]`
    - `find_plan(milestone: int, root: Path) -> Path`: the single match of `docs/plans/*-m{N}-*.md`. Raises `FileNotFoundError` on zero or several matches.
    - `task_id_from_title(title: str) -> str | None`
  - `flow.verdict`:
    - `@dataclass(frozen=True) class Verdict(result: Literal["PASS","FAIL"], sha: str)`
    - `format_verdict(result: Literal["PASS","FAIL"], sha: str, findings: str) -> str`
    - `parse_verdict(body: str) -> Verdict | None`
    - `latest_verdict_for(comments: list[str], sha: str) -> Verdict | None`. `comments` are in chronological order.
  - `flow.guard`:
    - `@dataclass(frozen=True) class PrState(number: int, head_sha: str, base: str, ci: Literal["success","failure","pending","missing"], comments: list[str], checkpoint: bool, checkpoint_approved: bool)`
    - `merge_blockers(state: PrState) -> list[str]`: an empty list means the merge is allowed.
    - `start_blockers(deps: list[DepState]) -> list[str]`, where `DepState(task_id: str, status: str | None, checkpoint: bool, approved: bool)`.
  - `flow.board`: `set_status(issue_number: int, status: str) -> None`. It looks up the project item and the Status field and option IDs by name through GraphQL, and adds the issue to the project if it's missing.
- Plan format contract (this plan follows it):
  - A task heading is `### M<N>.T<n> <title>`.
  - An optional `**Depends on:** M<N>.T<n>, …` line (`none` means no dependencies).
  - An optional `**Checkpoint**` line.
  - An `**Acceptance criteria**` line followed by `- [ ] ` items.
  - The section ends at the next `### ` or `---`.

- [ ] **Step 1: Set up the package.** `scripts/flow/pyproject.toml`:
  - name `agentops-flow`, Python 3.12, no runtime dependencies
  - `[project.scripts] flow = "flow.cli:main"`
  - dev dependencies `pytest`, `ruff`, `mypy` (strict)

  Make the shim executable.

- [ ] **Step 2: Write the failing plan tests** (`test_plan.py`). Use the fixture `plan_sample.md` with three tasks, one marked checkpoint and one with `Depends on: M9.T1, M9.T2`.

```python
def test_parse_plan_extracts_tasks_in_order():
    tasks = parse_plan(SAMPLE)
    assert [t.id for t in tasks] == ["M9.T1", "M9.T2", "M9.T3"]
    assert tasks[0].title == "First task"

def test_parse_plan_reads_acceptance_checkpoint_and_depends():
    t1, _, t3 = parse_plan(SAMPLE)
    assert t1.acceptance == ["Criterion one", "Criterion two"]
    assert t3.checkpoint is True and t1.checkpoint is False
    assert t3.depends_on == ["M9.T1", "M9.T2"]

def test_parse_plan_ignores_non_task_headings():
    assert all(t.id.startswith("M9.T") for t in parse_plan(SAMPLE + "\n### Notes\ntext\n"))

def test_task_id_from_title():
    assert task_id_from_title("M0.T3 Data model and first migration") == "M0.T3"
    assert task_id_from_title("fix typo") is None

def test_find_plan_errors_on_ambiguous(tmp_path):
    (tmp_path / "docs/plans").mkdir(parents=True)
    for n in ("a-m1-x.md", "b-m1-y.md"):
        (tmp_path / "docs/plans" / n).write_text("")
    with pytest.raises(FileNotFoundError):
        find_plan(1, tmp_path)
```

- [ ] **Step 3: Write the failing verdict and guard tests.**

```python
SHA = "a" * 40; OLD = "b" * 40

def test_format_then_parse_roundtrip():
    body = format_verdict("PASS", SHA, "- looks good")
    assert body.splitlines()[0] == f"Review verdict: PASS @ {SHA}"
    assert parse_verdict(body) == Verdict("PASS", SHA)

def test_parse_rejects_short_sha_and_other_comments():
    assert parse_verdict("Review verdict: PASS @ abc123") is None
    assert parse_verdict("LGTM") is None

def test_latest_verdict_for_head_wins():
    comments = [format_verdict("PASS", SHA, ""), format_verdict("FAIL", SHA, "")]
    assert latest_verdict_for(comments, SHA) == Verdict("FAIL", SHA)

def test_merge_allowed_when_green_and_pass_at_head():
    assert merge_blockers(state(ci="success", comments=[format_verdict("PASS", SHA, "")])) == []

@pytest.mark.parametrize("overrides, reason", [
    ({"ci": "pending"}, "ci is pending"),
    ({"ci": "failure"}, "ci is failure"),
    ({"comments": [format_verdict("PASS", OLD, "")]}, "no PASS verdict at head"),
    ({"comments": [format_verdict("PASS", SHA, ""), format_verdict("FAIL", SHA, "")]}, "latest verdict at head is FAIL"),
    ({"checkpoint": True, "checkpoint_approved": False}, "checkpoint not approved"),
    ({"base": "main"}, "base is not a milestone branch"),
])
def test_merge_blocked(overrides, reason):
    assert reason in merge_blockers(state(**overrides))

def test_start_blocked_by_unmerged_or_unapproved_dependency():
    assert start_blockers([DepState("M0.T3", "In review", True, False)]) != []
    assert start_blockers([DepState("M0.T3", "Merged to milestone", True, False)]) != []
    assert start_blockers([DepState("M0.T3", "Merged to milestone", True, True)]) == []
```

  `state(**overrides)` is a test helper. Its defaults are a green, merge-allowed `PrState` with base `milestone/m0` and head `SHA`. The exact reason strings above are part of the contract.

- [ ] **Step 4: Run the tests to see them fail.** Run `cd scripts/flow && uv run pytest -q`. Expected: ImportErrors.

- [ ] **Step 5: Implement `plan.py`, `verdict.py` and `guard.py`.** The verdict regex is `^Review verdict: (PASS|FAIL) @ ([0-9a-f]{40})$`, matched against the first line.

- [ ] **Step 6: Run the tests to see them pass.** Expected: all pass.

- [ ] **Step 7: Implement `gh.py`, `board.py` and `cli.py`** (argparse subcommands). Each command prints compact output, exits 0 on success, and on refusal exits 1 with one `✗ <reason>` line per blocker.
  - `status [N]`: one row per milestone issue: `#n  M0.T3  <status>  <branch|->  PR#<n|->  ci=<state>  verdict=<PASS|FAIL|->`.
  - `issue-start <n>`: checks `start_blockers` (dependencies come from the plan, statuses from the board, approval from the label on the dependency's issue). Then it runs `git fetch`, `git switch -c <n>-<slug> origin/milestone/m<N>` and `set_status(n, "In progress")`. The slug is the title after the task ID, kebab-cased and cut to 5 words.
  - `issue-pr <n> "<title>" [--notes FILE]`: pushes the branch and runs `gh pr create --base milestone/m<N>` with a body from `.github/PULL_REQUEST_TEMPLATE/issue.md`, filling in `Part of #n`, the plan link and the notes. Then `set_status(n, "In review")`.
  - `pr-gate <pr>`: `gh pr checks <pr> --watch --interval 20`. If a check fails, print each failing check's name, then `gh run view <run-id> --log-failed | tail -n 60`.
  - `review-context <pr>`: prints the sections `## Task <id>` (the plan section body), `## Acceptance criteria`, `## Head <sha>` and `## Diff` (`gh pr diff`).
  - `post-verdict <pr> PASS|FAIL <file> --sha <reviewed-sha>`: takes the reviewed SHA (printed by `review-context`). It reads the current head (`gh pr view --json headRefOid`), refuses with `✗ head moved: reviewed <sha7>, current <sha7>` if they differ, then runs `gh pr comment --body <format_verdict(...)>` with the given SHA.
  - `issue-merge <pr>`: builds `PrState`. CI is `success` only if every check is in the `pass` or `skipping` bucket, and `missing` if there are no checks. The checkpoint and approval flags come from the linked issue's labels. If there are no blockers, it runs `gh pr merge <pr> --squash --subject "<PR title>" --delete-branch` and `set_status(issue, "Merged to milestone")`.

  The issue number is parsed from `Part of #<n>` in the PR body.

- [ ] **Step 8: Write `.github/PULL_REQUEST_TEMPLATE/issue.md`** with these sections: `Part of #`, Summary, Plan section, Deviations from plan, Definition of done checklist (copied from `CONTRIBUTING.md`).

- [ ] **Step 9: Add flow to CI and `make check`.** In `ci.yml` add job `flow` (setup-uv, `uv sync --locked`, ruff check, ruff format --check, mypy, pytest, in `scripts/flow`). Append the same steps to `scripts/check.sh`.

- [ ] **Step 10: Verify against the live repo.** Run `./flow status 0`. Expected: one row per M0 issue, with M0.T1 showing `Merged to milestone`. Then open this PR with `./flow issue-pr <n> "feat(flow): add issue-loop workflow CLI"`, and run `./flow pr-gate <pr>`. Expected: exit 0.

- [ ] **Step 11: Commit** after each green cycle. For example: `git commit -m "feat(flow): parse plans, verdicts and merge guard"`.

---

### M0.T3 Data model and first migration

**Depends on:** M0.T1
**Checkpoint**

**Goal:** All nine entities as SQLAlchemy models with an Alembic migration that encodes the spec's constraints and deletion rules, plus the test database fixtures that later tasks use.

**Acceptance criteria**
- [ ] `make migrate` applies the migration to the dev database, and `alembic upgrade head → downgrade base → upgrade head` succeeds on the test database.
- [ ] The tables, columns, uniques, `CHECK`s and `ON DELETE` rules match spec section 6 and the Global Constraints.
- [ ] The constraint tests below pass.
- [ ] The author approves the PR (`checkpoint-approved` label) before M0.T6 and M0.T9 start.

**Files:**
- Create: `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/script.py.mako`, `backend/alembic/versions/0001_initial_schema.py`
- Create: `backend/app/models/__init__.py`, `base.py`, `agent.py` (Agent, AgentVersion), `dataset.py` (Dataset, TestCase), `evaluation.py` (EvaluationRun, EvaluationResult), `trace.py` (Trace, Span, Metric)
- Create: `backend/tests/models/test_migrations.py`, `backend/tests/models/test_constraints.py`
- Modify: `backend/tests/conftest.py`, `Makefile` (adds `migrate`)

**Interfaces:**
- Consumes: `Settings`, `create_app`, and `get_session` (M0.T1).
- Produces:
  - `app.models.Base` (a `DeclarativeBase`), with mixins `UUIDPrimaryKey` (`id: Mapped[uuid.UUID]`, server default `gen_random_uuid()`) and `Timestamps` (`created_at`, `updated_at`, `timestamptz`, server default `now()`, with `updated_at` set on update).
  - `app/models/__init__.py` re-exports all nine classes:
    - `Agent(name unique, description)`
    - `AgentVersion(agent_id, version, label, provider, model, system_prompt, parameters, tools, created_at)`
    - `Dataset(name unique, description)`
    - `TestCase(dataset_id, key, input, expected_behavior, expected_output, tags)`
    - `EvaluationRun`
    - `Trace`
    - `Span`
    - `EvaluationResult`
    - `Metric`

    Column names are as in spec section 6. Status values (my call):
    - `EvaluationRun.status` is one of `pending|running|completed|failed|cancelled`.
    - `Trace.status` is one of `running|completed|failed`.
    - `Span.status` is one of `unset|ok|error` (the OTel status codes).
    - `Span.kind` is one of `llm|tool|chain|other`.
  - Constraint names (used to map errors to 409 in M0.T6 and M0.T7):
    - `uq_agents_name`
    - `uq_agent_versions_agent_id_version`
    - `uq_datasets_name`
    - `uq_test_cases_dataset_id_key`
    - `uq_evaluation_results_run_case`
    - `uq_traces_otel_trace_id`
    - `uq_spans_trace_id_otel_span_id`

    Set a `MetaData(naming_convention=…)` on `Base`, so that autogenerate produces exactly these names.
  - Test fixtures:
    - `engine` (session-scoped `AsyncEngine` on the test DB, after `alembic upgrade head`, which runs in a **sync** session fixture before the engine is created)
    - `db_session` (one connection per test, an outer transaction rolled back at teardown, `AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)`)

    The `client` fixture now overrides `get_session` to yield `db_session`.

- [ ] **Step 1: Write the failing migration test.** `test_migrations.py::test_upgrade_downgrade_upgrade_roundtrip` is a plain `def`, not async, because `env.py` calls `asyncio.run`. It runs `command.upgrade(cfg, "head")`, `command.downgrade(cfg, "base")` and `command.upgrade(cfg, "head")`, with `cfg` pointing at `TEST_DATABASE_URL`. It then asserts that `inspect(sync_engine).get_table_names()` includes all of `{"agents","agent_versions","datasets","test_cases","evaluation_runs","traces","spans","evaluation_results","metrics"}`.

- [ ] **Step 2: Write the failing constraint tests** (`test_constraints.py`, using `db_session`). Each one inserts minimal rows through the ORM and asserts:
  - `test_agent_name_unique`: a second `Agent(name="a")` flush raises `IntegrityError` with `uq_agents_name` in the message.
  - `test_agent_version_unique_per_agent`: a duplicate `(agent_id, version=1)` raises `IntegrityError`, but `version=1` under two different agents is fine.
  - `test_provider_check_rejects_unknown`: `provider="mistral"` raises `IntegrityError`.
  - `test_deleting_agent_cascades_versions`: deleting the agent with Core `delete(Agent)` (not ORM cascade) leaves `select(count()).select_from(AgentVersion)` at 0.
  - `test_deleting_version_referenced_by_run_is_restricted`: deleting an Agent whose version has an EvaluationRun raises `IntegrityError` (FK violation).
  - `test_deleting_test_case_referenced_by_result_is_restricted`: the same pattern, with an EvaluationResult referencing the TestCase.
  - `test_deleting_trace_cascades_spans_and_metrics`: deleting a Trace leaves no rows in its Span or Metric tables.
  - `test_test_case_key_unique_per_dataset`

  Each test that expects an `IntegrityError` wraps the failing statement in `async with db_session.begin_nested():`, so the outer transaction survives.

- [ ] **Step 3: Run the tests to see them fail.** Run `uv run pytest tests/models -q`. Expected: errors, because the models and Alembic don't exist yet.

- [ ] **Step 4: Implement the models and Alembic.**
  - `alembic init -t async alembic`.
  - `env.py` uses `config.get_main_option("sqlalchemy.url")` if it's set, and otherwise `get_settings().database_url`. Its `target_metadata` is `Base.metadata`.
  - Generate `0001_initial_schema` with autogenerate. Hand-check it against spec section 6: the `server_default`s, the `CHECK`s, and `ondelete` on each FK per the Global Constraints.
  - Add `make migrate` (`cd backend && uv run alembic upgrade head`).

- [ ] **Step 5: Update `conftest.py`.** Add the `engine` and `db_session` fixtures and change the `client` override, as in the Interfaces block.

- [ ] **Step 6: Run the tests to see them pass.** Run `uv run pytest -q`. Expected: everything passes, including the health tests.

- [ ] **Step 7: Verify on the dev database.** Run `make migrate && docker compose exec db psql -U agentops -c '\dt'`. Expected: nine tables plus `alembic_version`.

- [ ] **Step 8: Commit, open the PR, then pause for the checkpoint.** Run `./flow issue-pr <n> "feat(db): add core data model and initial migration"`, `./flow pr-gate`, the review, and `./flow post-verdict`. Then **notify the author**. Don't merge until the PR has the `checkpoint-approved` label, which `./flow issue-merge` enforces.

---

### M0.T4 `flow` CLI: milestone commands

**Depends on:** M0.T2

**Goal:** Script milestone start and the milestone PR roll-up, so that M1 onward needs no manual setup.

**Acceptance criteria**
- [ ] `./flow milestone-start <N>` creates or updates one issue per plan task. It sets the title, body (plan link, goal, acceptance criteria), labels (`checkpoint` where marked), milestone, assignee, board status `Ready`, the branch `milestone/m<N>` if it's missing, and the draft milestone PR if it's missing. Running it again changes nothing.
- [ ] `./flow milestone-pr <N>` rewrites only the "Issue PRs in this milestone" and "Closes" sections of the milestone PR body, using merged issue PRs in merge order.
- [ ] Unit tests cover the issue-body rendering and the PR-body section replacement.
- [ ] `./flow milestone-start 0 --dry-run` against this plan lists all nine M0 issues as `unchanged`.

**Files:**
- Create: `scripts/flow/flow/milestone.py`, `scripts/flow/tests/test_milestone.py`, `docs/plans/TEMPLATE-milestone-plan.md`
- Modify: `scripts/flow/flow/cli.py`

**Interfaces:**
- Consumes: `Task` and `parse_plan`/`find_plan` (from T2), plus `gh` and `board.set_status` (from T2).
- Produces:
  - `render_issue_body(task: Task, plan_url: str) -> str`. The body includes the goal (the first paragraph after `**Goal:**`) and the acceptance criteria as `- [ ]` items.
  - `replace_section(body: str, heading: str, content: str) -> str`. It replaces the text between `## <heading>` and the next `## `, and leaves everything else byte-identical.
  - `render_rollup(prs: list[tuple[int, str]], issues: list[int]) -> tuple[str, str]` returns the text for the "Issue PRs" and "Closes" sections.

- [ ] **Step 1: Write the failing tests.**
  - `test_render_issue_body_contains_link_goal_and_criteria`
  - `test_replace_section_only_touches_named_section`: three sections. Replacing the middle one leaves the other two byte-identical.
  - `test_render_rollup_lists_prs_and_closes_lines`: the input `[(12, "feat: a"), (14, "feat: b")]`, `[3, 4]` produces `- #12 — feat: a\n- #14 — feat: b` and `Closes #3\nCloses #4`.
  - `test_milestone_start_is_idempotent`: uses a fake `gh` module recording calls. A second run against existing matching issues makes no `issue create` or `issue edit` calls.

- [ ] **Step 2: Run the tests to see them fail.** Run `uv run pytest tests/test_milestone.py -q`.

- [ ] **Step 3: Implement `milestone.py` and the CLI commands.** `milestone-start` matches issues by task ID in the title, within the milestone. It supports `--dry-run`, which prints `create|update|unchanged <id>`.

- [ ] **Step 4: Run the tests to see them pass.**

- [ ] **Step 5: Write `docs/plans/TEMPLATE-milestone-plan.md`.** It's a skeleton showing the plan format contract from M0.T2 (header, Global Constraints, Review Focus, task heading format, `**Depends on:**`, `**Checkpoint**`, `**Acceptance criteria**`). It is the one file under `docs/plans/` that `find_plan` must ignore. Its name has no `-m<N>-`, so the glob already skips it; add an assertion for that in `test_plan.py`.

- [ ] **Step 6: Verify.** Run `./flow milestone-start 0 --dry-run`. Expected: nine `unchanged` lines. If any show `update`, reconcile the issue (the plan wins).

- [ ] **Step 7: Commit and open the PR** with the title `feat(flow): add milestone-start and milestone-pr`.

---

### M0.T5 Agent workflow docs and skills

**Depends on:** M0.T2, M0.T4

**Goal:** Write down the workflow so a fresh agent can run a milestone with nothing but the repo.

**Acceptance criteria**
- [ ] `CONTRIBUTING.md`'s "Agent workflow", "Definition of done" and "Local commands" sections are complete, the placeholder comments are gone, and the typo "reqrite" is fixed.
- [ ] `AGENTS.md` is complete. It covers the roles, a one-line-per-command reference for `./flow` and `make`, the merge rule, and "never apply `checkpoint-approved`".
- [ ] `.claude/skills/orchestrate-milestone/SKILL.md` and `.claude/skills/review-issue-pr/SKILL.md` exist with frontmatter `name` and `description`, and content per spec section 11.2.
- [ ] A fresh subagent given only "read AGENTS.md and the review-issue-pr skill, then describe the steps to review PR #X" lists the correct `./flow` commands in order. Record its output in the PR.

**Files:**
- Modify: `AGENTS.md`, `CONTRIBUTING.md`
- Create: `.claude/skills/orchestrate-milestone/SKILL.md`, `.claude/skills/review-issue-pr/SKILL.md`

**Interfaces:**
- Consumes: the CLI commands and reason strings from M0.T2 and M0.T4, and the Makefile targets from M0.T1, M0.T3, M0.T7, M0.T8 and M0.T9. Write `make seed`, `make api-types`, `make up` and `make down` into Local commands now, even if they land later.

- [ ] **Step 1: Write the Agent workflow section of `CONTRIBUTING.md`.** Cover:
  - **Roles:** Orchestrator, Implementer, Reviewer, and when a fresh Reviewer is needed (native implementation).
  - **Records:** the gate record and the review record, with the verdict header format.
  - **SHA invalidation:** any new push invalidates the gate and verdict.
  - **FAIL loop:** a FAIL blocks the merge until the fix is pushed and the PR passes the gate and a new review.
  - **Checkpoints:** approval is the `checkpoint-approved` label, which only the author applies. All PRs are opened under the author's GitHub account, so a GitHub review approval is impossible.
  - **`./flow` is authoritative.**

  Then write Definition of done:
  - backend: `ruff check`, `ruff format --check`, `mypy`, `pytest`
  - frontend: `eslint`, `tsc`, `vitest`, `next build`
  - `make check` green
  - generated API types up to date

  And Local commands: a table of the Makefile targets and `./flow` commands.

- [ ] **Step 2: Write `AGENTS.md`** in under 80 lines. It should link to `CONTRIBUTING.md`, not repeat it.

- [ ] **Step 3: Write the two skills.**
  - `orchestrate-milestone`: the loop, the dispatch prompt checklist (issue number, plan path and section, "use `./flow issue-start`, TDD, `make check`, `./flow issue-pr`"), handling checkpoints, and finishing with `milestone-pr` and a full `make check` on the milestone branch. It defers general dispatch practice to `superpowers:subagent-driven-development`.
  - `review-issue-pr`: `review-context`, then check scope against the plan section and acceptance criteria. A FAIL is:
    - an unmet acceptance criterion
    - a broken Global Constraint
    - a missing test for stated behaviour
    - a correctness bug

    A minor finding is deferred, and gets a backlog issue on the right open milestone. Then post with `post-verdict`.

- [ ] **Step 4: Verify.** Dispatch a fresh subagent with the prompt from the last acceptance criterion and compare its steps against the skill. Fix any ambiguity it shows.

- [ ] **Step 5: Commit and open the PR** with the title `docs: complete agent workflow, AGENTS.md and role skills`.

---

### M0.T6 Agents API

**Depends on:** M0.T3

**Goal:** CRUD for agents, create/list/get for immutable versions, the shared pagination and error plumbing, and a services layer that the seed script reuses.

**Acceptance criteria**
- [ ] The endpoints in spec section 7 for agents and versions behave as tested below.
- [ ] Deleting an agent with evaluation history returns 409, and nothing is deleted.
- [ ] Creating two versions for one agent at the same time yields versions 1 and 2, never a 500.
- [ ] `/openapi.json` documents every endpoint with its request and response schemas.

**Files:**
- Create: `backend/app/errors.py`, `backend/app/schemas/__init__.py`, `common.py`, `agent.py`, `backend/app/services/__init__.py`, `agents.py`, `backend/app/api/agents.py`, `backend/tests/api/test_agents.py`, `backend/tests/services/test_agent_versions.py`
- Modify: `backend/app/main.py` (mounts the router and registers error handlers)

**Interfaces:**
- Consumes: `Agent`, `AgentVersion`, the constraint names, and the `client`/`db_session`/`engine` fixtures (M0.T3).
- Produces:
  - `app.errors`: `NotFoundError(detail: str)` (404) and `ConflictError(detail: str)` (409), with handlers registered in `create_app`. `integrity_conflict(exc: IntegrityError) -> ConflictError` maps the constraint names to messages, e.g. `"Agent name already exists"`. A FK violation becomes `"<Entity> is referenced by evaluation history"`.
  - `app.schemas.common`:
    - `Page[T](BaseModel)` with `items: list[T]`, `total: int`, `limit: int`, `offset: int`
    - `PageParams` dependency with `limit: int = Query(50, ge=1, le=200)` and `offset: int = Query(0, ge=0)`
  - `app.schemas.agent`:
    - `AgentCreate(name: str (1–200, stripped), description: str | None = None)`
    - `AgentUpdate(name: str | None, description: str | None)`
    - `AgentRead(id, name, description, created_at, updated_at)`
    - `AgentVersionCreate(label: str | None = None, provider: Literal["anthropic","openai"], model: str (1–200), system_prompt: str, parameters: dict[str, Any] = {}, tools: list[str] = [])`
    - `AgentVersionRead(id, agent_id, version: int, label, provider, model, system_prompt, parameters, tools, created_at)`
  - `app.services.agents`:
    - `create_agent(session, data) -> Agent`
    - `list_agents(session, limit, offset) -> tuple[list[Agent], int]`
    - `get_agent(session, agent_id) -> Agent`, which raises `NotFoundError`
    - `update_agent(session, agent_id, data) -> Agent`
    - `delete_agent(session, agent_id) -> None`
    - `create_version(session, agent_id, data) -> AgentVersion`
    - `list_versions(session, agent_id, limit, offset) -> tuple[list[AgentVersion], int]`
    - `get_version(session, agent_id, version: int) -> AgentVersion`
    - `get_agent_by_name(session, name) -> Agent | None`

    Writes that can conflict run inside `async with session.begin_nested():` and convert `IntegrityError` with `integrity_conflict`. Services `flush`; `get_session` commits.
  - `create_version` locks the agent row (`select(Agent).where(...).with_for_update()`), then uses `coalesce(max(version), 0) + 1`.

- [ ] **Step 1: Write the failing API tests** (`test_agents.py`):

```python
async def test_create_and_get_agent(client):
    r = await client.post("/api/v1/agents", json={"name": "weather-bot", "description": "d"})
    assert r.status_code == 201
    agent = r.json()
    assert (await client.get(f"/api/v1/agents/{agent['id']}")).json()["name"] == "weather-bot"

async def test_duplicate_agent_name_is_409(client):
    await client.post("/api/v1/agents", json={"name": "dup"})
    r = await client.post("/api/v1/agents", json={"name": "dup"})
    assert r.status_code == 409 and r.json() == {"detail": "Agent name already exists"}

async def test_rename_to_existing_name_is_409(client): ...   # create "a" and "b"; PATCH b name="a" -> 409

async def test_list_agents_paginates(client):
    for i in range(3): await client.post("/api/v1/agents", json={"name": f"a{i}"})
    body = (await client.get("/api/v1/agents?limit=2&offset=1")).json()
    assert body["total"] == 3 and body["limit"] == 2 and body["offset"] == 1 and len(body["items"]) == 2

@pytest.mark.parametrize("qs", ["limit=0", "limit=201", "offset=-1"])
async def test_list_agents_rejects_bad_pagination(client, qs):
    assert (await client.get(f"/api/v1/agents?{qs}")).status_code == 422

async def test_unknown_agent_is_404(client):
    assert (await client.get(f"/api/v1/agents/{uuid4()}")).status_code == 404

async def test_versions_are_numbered_and_immutable(client):
    a = (await client.post("/api/v1/agents", json={"name": "v"})).json()
    body = {"provider": "anthropic", "model": "claude-sonnet-5-5", "system_prompt": "p"}
    v1 = (await client.post(f"/api/v1/agents/{a['id']}/versions", json=body)).json()
    v2 = (await client.post(f"/api/v1/agents/{a['id']}/versions", json=body)).json()
    assert (v1["version"], v2["version"]) == (1, 2)
    assert (await client.patch(f"/api/v1/agents/{a['id']}/versions/1", json={})).status_code == 405
    assert (await client.get(f"/api/v1/agents/{a['id']}/versions/2")).json()["id"] == v2["id"]

async def test_unknown_provider_is_422(client): ...          # provider="mistral" -> 422

async def test_delete_agent_cascades_versions(client): ...   # DELETE -> 204; GET -> 404

async def test_delete_agent_with_history_is_409(client, db_session):
    # create agent+version via API; insert Dataset + EvaluationRun on that version via db_session
    r = await client.delete(f"/api/v1/agents/{agent_id}")
    assert r.status_code == 409
    assert (await client.get(f"/api/v1/agents/{agent_id}")).status_code == 200
```

- [ ] **Step 2: Write the failing concurrency test** (`tests/services/test_agent_versions.py`). It uses `engine` with two independent sessions, not `db_session`. It creates an agent and commits, then runs `asyncio.gather(create_version(s1, …), create_version(s2, …))` with each session committing. It asserts the versions are `{1, 2}`. In a `finally`, it deletes the agent with a fresh session and commits.

- [ ] **Step 3: Run the tests to see them fail.** Run `uv run pytest tests/api/test_agents.py tests/services -q`.

- [ ] **Step 4: Implement the code.** Write the errors, schemas, services and router. Status codes: POST returns 201, DELETE returns 204. Versions have no PATCH or DELETE route, so FastAPI returns 405 for them.

- [ ] **Step 5: Run the tests to see them pass.** Then run `make check`. Expected: all `✓`.

- [ ] **Step 6: Commit and open the PR** with the title `feat(api): add agents and agent versions endpoints`.

---

### M0.T7 Datasets API, seed data, architecture doc

**Depends on:** M0.T6

**Goal:** Dataset and test-case endpoints, including all-or-nothing bulk create, an idempotent sample seed, and the documented data model and API.

**Acceptance criteria**
- [ ] The dataset and test-case endpoints in spec section 7 behave as tested below.
- [ ] Bulk create is all-or-nothing: a duplicate key within the payload or against existing cases returns 409, and nothing is inserted.
- [ ] Deleting a test case or dataset referenced by a Trace or EvaluationResult returns 409.
- [ ] `make seed` loads `datasets/sample.json`, and a second run creates nothing new.
- [ ] `docs/architecture.md` has a Mermaid ERD of all nine entities (matching the migration) and an API overview pointing to `/docs`.

**Files:**
- Create: `backend/app/schemas/dataset.py`, `backend/app/services/datasets.py`, `backend/app/api/datasets.py`, `backend/app/seed.py`, `datasets/sample.json`, `docs/architecture.md`
- Create: `backend/tests/api/test_datasets.py`, `backend/tests/test_seed.py`
- Modify: `backend/app/main.py`, `backend/app/errors.py` (dataset and test-case constraint messages), `Makefile` (adds `seed`), `README.md` (links the architecture doc)

**Interfaces:**
- Consumes: `Page`, `PageParams`, `NotFoundError`, `ConflictError`, `integrity_conflict`, and `app.services.agents` (M0.T6).
- Produces:
  - `DatasetCreate(name, description)`, `DatasetUpdate`, `DatasetRead`. These mirror the agent schemas.
  - `TestCaseCreate(key: str matching ^[A-Za-z0-9_.-]{1,100}$, input: str (min 1), expected_behavior: list[str] = [], expected_output: str | None = None, tags: list[str] = [])`
  - `TestCaseUpdate`, which has all fields optional.
  - `TestCaseRead(id, dataset_id, key, input, expected_behavior, expected_output, tags, created_at, updated_at)`
  - `app.services.datasets` mirrors the agent services: `create_dataset`, `list_datasets`, `get_dataset`, `get_dataset_by_name`, `update_dataset`, `delete_dataset`, `create_test_case`, `bulk_create_test_cases(session, dataset_id, items: list[TestCaseCreate]) -> list[TestCase]`, `list_test_cases`, `get_test_case`, `update_test_case`, `delete_test_case`, and `get_test_case_by_key(session, dataset_id, key) -> TestCase | None`.
  - Routes:
    - `POST /api/v1/datasets/{dataset_id}/test-cases:bulk` takes `list[TestCaseCreate]` (1–1000 items) and returns 201 with `list[TestCaseRead]`.
    - `/test-cases/{case_id}` takes a UUID.
  - `app.seed.seed(session: AsyncSession, path: Path) -> SeedReport`, where `SeedReport(created: int, existing: int)`. CLI: `python -m app.seed <path>`. `make seed` runs it against `DATABASE_URL`.
  - `datasets/sample.json`: `{"agents": [{"name", "description", "versions": [AgentVersionCreate]}], "datasets": [{"name", "description", "test_cases": [TestCaseCreate]}]}`. It holds one agent with one version (`anthropic`/`claude-sonnet-5-5`), and the dataset `weather-basics` with 3 cases in the M2 example format, starting with `case_001` "Find the weather in Seattle".

- [ ] **Step 1: Write the failing API tests** (`test_datasets.py`). Mirror the agent tests for create, get, list, 404, duplicate name and rename (409). Then add:

```python
async def test_bulk_create_test_cases(client, dataset):
    items = [{"key": f"case_{i:03}", "input": "q", "expected_behavior": ["b"]} for i in range(3)]
    r = await client.post(f"/api/v1/datasets/{dataset['id']}/test-cases:bulk", json=items)
    assert r.status_code == 201 and [c["key"] for c in r.json()] == ["case_000", "case_001", "case_002"]

async def test_bulk_duplicate_in_payload_inserts_nothing(client, dataset):
    items = [{"key": "k", "input": "q"}, {"key": "k", "input": "q"}]
    assert (await client.post(f".../{dataset['id']}/test-cases:bulk", json=items)).status_code == 409
    assert (await client.get(f".../{dataset['id']}/test-cases")).json()["total"] == 0

async def test_bulk_duplicate_against_existing_inserts_nothing(client, dataset): ...  # existing "k"; bulk ["new","k"] -> 409; total == 1

async def test_invalid_case_key_is_422(client, dataset): ...      # key "has space" -> 422

async def test_delete_case_referenced_by_result_is_409(client, db_session, dataset): ...
async def test_delete_dataset_cascades_unreferenced_cases(client, dataset): ...
```

  `dataset` is a fixture that creates a dataset via the API.

- [ ] **Step 2: Write the failing seed test** (`test_seed.py`). The first `seed(db_session, SAMPLE)` returns `created > 0`. The second returns `created == 0`. The test then asserts that `get_dataset_by_name(db_session, "weather-basics")` has 3 test cases.

- [ ] **Step 3: Run the tests to see them fail.**

- [ ] **Step 4: Implement the schemas, services, router, seed and `make seed`.** Bulk create first checks the payload for duplicate keys and raises `ConflictError("Duplicate test case key in request: <key>")`. Then it inserts every row inside one `begin_nested()`, so a conflict with existing rows rolls back the whole batch.

- [ ] **Step 5: Run the tests to see them pass.** Then run `make migrate && make seed && make seed`. Expected: the second run reports `created=0`.

- [ ] **Step 6: Write `docs/architecture.md`.** Include:
  - an overview diagram (browser → Next.js → FastAPI → Postgres)
  - a Mermaid `erDiagram` of all nine entities with their key fields and relationships
  - the deletion rules
  - an endpoint table that links to `/docs`

  Link it from `README.md`.

- [ ] **Step 7: Commit and open the PR** with the title `feat(api): add datasets and test cases endpoints with sample seed`.

---

### M0.T8 Frontend API contract and home page

**Depends on:** M0.T7

**Goal:** Typed frontend access to the API, generated from OpenAPI with a CI drift check, and a home page proving frontend→backend connectivity.

**Acceptance criteria**
- [ ] `make api-types` regenerates `frontend/lib/api/openapi.json` and `frontend/lib/api/schema.ts`. Both are committed.
- [ ] The `ci` job `contracts` fails if the regenerated files differ from the committed ones.
- [ ] With the API running and seeded, the home page shows `Database: ok` and lists the seeded agent and dataset names. With the API down it shows `API unreachable`.
- [ ] Component tests cover the success, empty, and error states.

**Files:**
- Create: `backend/app/export_openapi.py`, `frontend/lib/api/openapi.json`, `frontend/lib/api/schema.ts`, `frontend/lib/api/client.ts`, `frontend/components/home-overview.tsx`, `frontend/components/__tests__/home-overview.test.tsx`
- Modify: `frontend/app/page.tsx`, `frontend/app/__tests__/page.test.tsx`, `.github/workflows/ci.yml`, `Makefile` (adds `api-types`), `scripts/check.sh`, `frontend/package.json`

**Interfaces:**
- Consumes: the OpenAPI spec from `create_app()` (M0.T6 and M0.T7).
- Produces:
  - `python -m app.export_openapi` prints `json.dumps(create_app().openapi(), indent=2, sort_keys=True)` to stdout. It needs no database.
  - `frontend/lib/api/client.ts`:
    - `export const api = createClient<paths>({ baseUrl: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000" })`
    - `export async function fetchOverview(): Promise<Overview>`, where `type Overview = { database: "ok" | "error"; agents: string[]; datasets: string[] }`. It fetches `/health`, `/api/v1/agents?limit=50`, and `/api/v1/datasets?limit=50`, and throws if the API can't be reached.
  - `components/home-overview.tsx`: `"use client"`, `export function HomeOverview({ load = fetchOverview }: { load?: () => Promise<Overview> })`. It renders `Database: ok|error`, a list of agents, a list of datasets, and `No agents yet` / `No datasets yet` when they're empty. When `load` rejects, it renders `API unreachable`.

- [ ] **Step 1: Add `make api-types`.** It runs `cd backend && uv run python -m app.export_openapi > ../frontend/lib/api/openapi.json`, then `cd frontend && npx openapi-typescript lib/api/openapi.json -o lib/api/schema.ts`. Add `openapi-typescript` (dev) and `openapi-fetch` to `package.json`. Run it, and check that `schema.ts` contains `"/api/v1/agents"`.

- [ ] **Step 2: Write the failing component tests** (`home-overview.test.tsx`). Inject `load` stubs:
  - `renders database status and names`: `load` resolves to `{database:"ok", agents:["weather-bot"], datasets:["weather-basics"]}`. `findByText("Database: ok")`, `"weather-bot"` and `"weather-basics"` are all present.
  - `renders empty states`: `"No agents yet"` and `"No datasets yet"` are present.
  - `renders error when api unreachable`: `load` rejects, and `"API unreachable"` is present.

- [ ] **Step 3: Run the tests to see them fail.** Run `npm test`.

- [ ] **Step 4: Implement `client.ts` and `HomeOverview`, and update `page.tsx`.** The page renders the `AgentOps` heading and `<HomeOverview />`. Keep the T1 smoke test passing by mocking `@/lib/api/client` in `page.test.tsx`.

- [ ] **Step 5: Run the tests to see them pass.** Then run `make check`.

- [ ] **Step 6: Add the `contracts` CI job.** It runs setup-uv and setup-node, then `uv sync --locked`, `npm ci`, `make api-types`, and `git diff --exit-code frontend/lib/api`. Add the same drift check to `scripts/check.sh`.

- [ ] **Step 7: Verify manually.** Run `make db migrate seed dev`, open http://localhost:3000, and confirm the seeded names and `Database: ok`. Stop the API and reload: it shows `API unreachable`. Put a screenshot in the PR.

- [ ] **Step 8: Commit and open the PR** with the title `feat(frontend): add generated API client and home overview`.

---

### M0.T9 Full Docker profile

**Depends on:** M0.T3

**Goal:** Run the whole stack in containers with one command, and build both images in CI.

**Acceptance criteria**
- [ ] `make up` (`docker compose --profile full up -d --build --wait`) starts `db`, `api`, and `web`. The api container applies migrations on start. `curl localhost:8000/health` returns `database: ok`, and http://localhost:3000 loads.
- [ ] `make down` stops everything.
- [ ] Both images run as a non-root user.
- [ ] The `ci` job `docker` builds both images (no push).

**Files:**
- Create: `backend/Dockerfile`, `backend/.dockerignore`, `backend/docker-entrypoint.sh`, `frontend/Dockerfile`, `frontend/.dockerignore`
- Modify: `docker-compose.yml`, `frontend/next.config.ts` (`output: "standalone"`), `Makefile` (adds `up`, and `down` gains `--profile full`), `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `make migrate` semantics (M0.T3), and `NEXT_PUBLIC_API_URL` (M0.T1).
- Produces:
  - Compose services:
    - `api`: profile `full`, build `./backend`, env `DATABASE_URL=postgresql+psycopg://agentops:agentops@db:5432/agentops`, depends on `db` (healthy), port 8000, healthcheck on `/health`.
    - `web`: profile `full`, build `./frontend` with build arg `NEXT_PUBLIC_API_URL=http://localhost:8000`, port 3000, depends on `api`.
  - `docker-entrypoint.sh` runs `alembic upgrade head`, then `exec uvicorn app.main:app --host 0.0.0.0 --port 8000`.

- [ ] **Step 1: Write the Dockerfiles.**
  - `backend/Dockerfile` is a multi-stage build using `ghcr.io/astral-sh/uv` for `uv sync --locked --no-dev`, then `python:3.12-slim`. It copies `.venv`, `app` and `alembic`, uses `USER app`, and the entrypoint above.
  - `frontend/Dockerfile` uses `node:22-alpine` for the deps stage, then the build stage (with the `NEXT_PUBLIC_API_URL` arg), then a standalone runner as `USER node`.

- [ ] **Step 2: Add the `full` profile services** to `docker-compose.yml`, and the `up`/`down` Makefile targets.

- [ ] **Step 3: Verify.**
  - `make down && make up`, then `curl -s localhost:8000/health`. Expected: `{"status":"ok","database":"ok"}`.
  - `curl -s -o /dev/null -w '%{http_code}' localhost:3000`. Expected: `200`.
  - `docker compose exec api whoami`. Expected: `app`.

- [ ] **Step 4: Add the `docker` CI job.** It runs `docker/setup-buildx-action` and `docker/build-push-action` twice, with `push: false` and GHA cache.

- [ ] **Step 5: Commit and open the PR** with the title `feat(infra): add full docker compose profile and image builds`.

---

## Milestone finish (Orchestrator)

1. On `milestone/m0`: run `make check`, `make down && make up` and the smoke checks from M0.T9, then `make seed` and the home page check from M0.T8.
2. Run `./flow milestone-pr 0`. Then fill in the Summary, Verification (the command outputs above), Deviations (including the `./flow` shim and checkpoint-label changes from the spec), and the Review guide. Tick the exit criteria:
   - [ ] The application starts locally
   - [ ] It connects to PostgreSQL
   - [ ] It supports initial agent and dataset records
3. Mark the PR ready for review and notify the author.
