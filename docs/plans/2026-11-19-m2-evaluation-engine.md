# M2 — Evaluation Engine: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Follow `CONTRIBUTING.md` for branches, PRs, and records.

**Goal:** `POST /api/v1/evaluation-runs` submits a dataset to run against an AgentVersion. A separate worker process runs every test case through M1's `run_agent()` and stores one result per case. Each result holds a snapshot of the case, the agent's answer and a link to its trace. The run's progress and results can be read back through the API.

**Architecture:**
- `evaluation_runs` is the queue. `app/evaluation/queue.py` claims runs with `SELECT … FOR UPDATE SKIP LOCKED`, keeps a heartbeat, and reclaims stale runs.
- `app/evaluation/runner.py` executes one claimed run with bounded concurrency. Each case gets its own session and commits its trace and its result together.
- `app/worker.py` is the process: `python -m app.worker`.
- The API only creates, lists, reads and cancels runs. It never executes cases.
- A provider failure is classified on `ProviderAPIError` and carried on `RunOutcome`. The runner uses it to record `error` results and to abort the run on account-level failures.

**Tech Stack:** M1's stack (Python 3.12, uv, FastAPI, SQLAlchemy 2.0 async, psycopg 3, Alembic, Pydantic v2, pytest, LangGraph, OpenTelemetry). No new runtime dependencies.

**Spec:** [`docs/specs/2026-11-19-m2-evaluation-engine-design.md`](../specs/2026-11-19-m2-evaluation-engine-design.md). "D4" and similar refer to its section 4 decisions.

## Global Constraints

- All M0 and M1 constraints still hold:
  - routes under `/api/v1`
  - pagination `{items, total, limit, offset}` (limit default 50, max 200)
  - errors `{"detail": …}` with 404, 409, 422 and 503
  - enums as `text` + `CHECK`
  - tests against real Postgres
  - generated frontend types committed (`make api-types`)
- **No test, `make check` step or `ci` job calls a model API or needs `ANTHROPIC_API_KEY`.** Only `make record`, `make smoke` and `make eval` call it, and **only the author runs them**. No agent runs them or asks for the key.
- Spans still reach the database only through `ingest_spans`. A trace is linked to its run and case by root-span attributes, never by updating the row afterwards (M1 D2, spec 6.1).
- The API never executes cases. Only the worker (`app.evaluation.runner`) does (D1).
- Result statuses are exactly `completed`, `failed` and `error`. A `completed` or `failed` result always has a `trace_id` (D3).
- A provider failure means `ProviderAPIError` with `kind` set (M2.T2). `kind == "account"` aborts the run. `kind == "unavailable"` records an `error` result and the run continues. A `ProviderResponseError`, or a `ProviderAPIError` with `kind is None`, is an agent `failed` (D10).
- No database transaction stays open while a model is called (M1 rule, `services/runs.py`).
- Settings: `eval_concurrency: int = 4` (≥ 1) and `eval_stale_after_seconds: int = 60` (≥ 10), read from `EVAL_CONCURRENCY` and `EVAL_STALE_AFTER_SECONDS`.
- A `completed` run has exactly one result for every case recorded in `total_cases` (anti-goal: quietly missing results).

## Review Focus

1. **Two workers poll at the same moment:** each run is claimed by exactly one of them, and a run with a fresh heartbeat is never reclaimed. (Test in M2.T3.)
2. **The worker dies after some cases have results:** the reclaimed run runs only the missing cases, no case gets two results, and `total_cases` is unchanged. (Test in M2.T4.)
3. **The provider returns 401 on the first case of a 25-case run:** the run ends `failed` with the provider's message, no new cases start, and in-flight cases still store their results. (Test in M2.T4.)
4. **A case is edited after its run:** the result still shows the input and expectations it ran with. (Test in M2.T4.)
5. **Cancel arrives while a run is `pending`, or races the worker's claim:** the run ends `cancelled` and never `running` forever. Cancelling a finished run is a 409. (Tests in M2.T3 and M2.T5.)

---

## Milestone start (Orchestrator, once)

1. Reconcile the existing M2 issues so that `milestone-start` updates them instead of creating duplicates. It matches on the `M2.T<n>` title prefix. Run `gh issue edit <n> --title "<new title>"` for each:
   - #51 → `M2.T8 Test factories, empty-key and key-rename coverage`
   - #47 → `M2.T9 Agent adapter, runtime and settings hardening`
   - #46 → `M2.T10 flow follow-ups from the M1 reviews`
2. #25 is folded into M2.T8. Comment "Folded into M2.T8 (#51)" on #25 and close it.
3. Update the roadmap's M2 row (`Plan` column → link to this plan) and add an `## M2 issues` table, like M1's.
4. Make sure the spec, this plan and the roadmap update exist in the working tree. Run `./flow milestone-start 2 --dry-run` and check it shows 3 updates and 7 creates. Then run `./flow milestone-start 2`.
5. Suggested order (one Implementer at a time): T1 (checkpoint). While it waits for the author, do T2, T7 and T10. Then T3 → T4 → T5 → T6, with T8 and T9 fitted in wherever there is a wait.

---

## Tasks

### M2.T1 Evaluation storage: migration 0003, models and trace links

**Depends on:** none
**Checkpoint**

**Goal:** Migration `0003` gives runs their queue columns and gives results their status, snapshot and output (spec 6.3). Ingest links a trace to its evaluation run and test case from the new root-span attributes. This fixes the result shape that M3, M4 and M5 read.

**Acceptance criteria**
- [ ] `evaluation_runs` gains `total_cases int NULL`, `heartbeat_at timestamptz NULL` and `cancel_requested bool NOT NULL DEFAULT false`, plus an index on `(status, created_at)`.
- [ ] `evaluation_results` gains:
  - `status text NOT NULL` with `CHECK status IN ('completed','failed','error')`
  - `error text NULL` and `output text NULL`
  - `case_key text NOT NULL`, `case_input text NOT NULL`, `case_expected_behavior jsonb NOT NULL`, `case_expected_output text NULL` and `case_tags jsonb NOT NULL`
  - `passed` becomes nullable
  - `CHECK (status = 'error' OR trace_id IS NOT NULL)`, named `trace_required`
- [ ] Migration round-trip (upgrade, downgrade, upgrade) passes. The downgrade refuses with a clear message if any result has `passed IS NULL`, rather than failing on the constraint.
- [ ] `attributes.py` defines `EVALUATION_RUN_ID = "agentops.evaluation_run_id"` and `TEST_CASE_ID = "agentops.test_case_id"`, documented as optional, root span only. `ingest_spans` copies them onto `Trace.evaluation_run_id` and `Trace.test_case_id`. A malformed UUID is an `IngestError`, like `AGENT_VERSION_ID`.
- [ ] `tests/factories.py` gains `make_dataset`, `make_evaluation_run` and `make_trace` (signatures below).
- [ ] `docs/architecture.md`: the ERD and the status tables match the new columns.
- [ ] The author approves the PR (`checkpoint-approved` label) before dependents start.

**Files:**
- Create: `backend/alembic/versions/0003_evaluation_engine.py`
- Modify: `backend/app/models/evaluation.py`, `backend/app/observability/attributes.py`, `backend/app/observability/ingest.py`, `backend/tests/factories.py`, `backend/tests/models/test_constraints.py`, `backend/tests/models/test_migrations.py`, `backend/tests/observability/test_ingest.py`, `docs/architecture.md`

**Interfaces:**
- Produces:
  - Model fields named exactly as the columns above.
  - `RESULT_STATUSES = ("completed", "failed", "error")` and `RUN_STATUSES = ("pending", "running", "completed", "failed", "cancelled")` in `app/models/evaluation.py`.
  - In `tests/factories.py`:
    - `async def make_dataset(session, *, cases: int = 3) -> Dataset`: unique name, cases keyed `case_001`… with `expected_behavior=["does the thing"]`.
    - `async def make_evaluation_run(session, *, version: AgentVersion | None = None, dataset: Dataset | None = None, **fields) -> EvaluationRun`: creates the missing parents.
    - `async def make_trace(session, *, version: AgentVersion | None = None, **fields) -> Trace`: a completed trace with a random `otel_trace_id`.

- [ ] **Step 1: Write the failing tests.**
  - `test_constraints.py::test_result_error_may_omit_trace` and `::test_result_completed_requires_trace` (IntegrityError on `trace_required`). `::test_result_status_check` (`status="ok"` → IntegrityError).
  - `test_constraints.py::test_result_passed_nullable`.
  - `test_ingest.py::test_root_links_evaluation_run_and_case`: a root with both attributes stores both ids on the trace. `::test_root_bad_evaluation_run_id_is_ingest_error`.
  - `test_migrations.py::test_round_trip` still passes.
- [ ] **Step 2: Run** `cd backend && uv run pytest tests/models tests/observability -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Write migration `0003`, the model fields and the attribute constants, then update `_trace_values`.**
- [ ] **Step 4: Add the factories.** Run the tests again. Expected: PASS.
- [ ] **Step 5: Update `docs/architecture.md`. Run `make check`, then `./flow issue-pr <n> "feat(evaluation): add migration 0003 for runs, results and trace links"`.** In `--summary`, list each column and constraint so the author can review the shape.

---

### M2.T2 Provider failure classification and richer run outcomes

**Depends on:** none

**Goal:** Tell provider outages apart from agent failures (D10), and give the runner what it needs from a run: the answer, the error, and whether the provider was at fault. `run_agent` also links the trace to an evaluation through the M2.T1 attributes.

**Acceptance criteria**
- [ ] `ProviderAPIError` has `kind: Literal["account", "unavailable"] | None`.
  - `"account"`: status 401, 402 or 403, or error type `authentication_error`, `permission_error` or `billing_error`.
  - `"unavailable"`: transient errors that outlast the retries (429, 5xx, `overloaded_error`), plus `APIConnectionError` and `APITimeoutError`.
  - Anything else (for example 400 or 404): `None`.
- [ ] `RunOutcome` gains `output: str | None`, `error: str | None` and `provider_failure: Literal["account", "unavailable"] | None`. `provider_failure` is set only when the run failed because of a `ProviderAPIError` with that `kind`.
- [ ] `run_agent` takes keyword-only `evaluation_run_id: UUID | None = None` and `test_case_id: UUID | None = None`, and sets them as root-span attributes when given.
- [ ] Existing M1 tests still pass unchanged, apart from `RunOutcome` construction in tests.

**Files:**
- Modify: `backend/app/agents/providers/base.py`, `backend/app/agents/providers/anthropic.py`, `backend/app/agents/graph.py`, `backend/app/agents/runtime.py`, `backend/tests/agents/test_anthropic_provider.py`, `backend/tests/agents/test_runtime.py`

**Interfaces:**
- Consumes: `attr.EVALUATION_RUN_ID` and `attr.TEST_CASE_ID` (M2.T1). If T1 hasn't merged yet, add the same two constants with the same docstrings directly after `LLM_ATTEMPTS` in `attributes.py`, which is where T1 puts them, so the two changes are identical.
- Produces:
  - `class ProviderAPIError(ProviderError): def __init__(self, message: str, *, kind: Literal["account","unavailable"] | None = None)`, with the value on `.kind`.
  - `@dataclass(frozen=True) class RunOutcome: trace_id: UUID; status: Literal["completed","failed"]; output: str | None = None; error: str | None = None; provider_failure: Literal["account","unavailable"] | None = None`
  - `async def run_agent(*, session, provider, config, input, evaluation_run_id: UUID | None = None, test_case_id: UUID | None = None) -> RunOutcome`
  - Graph state gains `provider_failure`, set in the model node's `except ProviderError` branch from `getattr(exc, "kind", None)`.

- [ ] **Step 1: Write the failing tests.**
  - `test_anthropic_provider.py::test_kind_account` (parametrised: 401, 403 and a 402 with `billing_error`) → `kind == "account"`, one attempt.
  - `::test_kind_unavailable_after_retries` (three 529s) → `"unavailable"`. `::test_kind_connection_error` → `"unavailable"`. `::test_kind_none_for_400` → `None`.
  - `test_runtime.py::test_outcome_carries_output` (`end_turn("Paris")` → `output == "Paris"`, `error is None`).
  - `::test_outcome_provider_failure` (`ScriptedProvider([ProviderAPIError("x", kind="account")])` → `status == "failed"`, `provider_failure == "account"`, `error` starts with `"provider error:"`).
  - `::test_response_error_is_agent_failure` (`ProviderResponseError` → `provider_failure is None`).
  - `::test_trace_linked_to_evaluation` (ids passed → the stored Trace has them).
- [ ] **Step 2: Run** `cd backend && uv run pytest tests/agents -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement the classification in `AnthropicProvider.complete`** (a `_kind(exc)` helper beside `_is_transient`), then the graph state, `RunOutcome` and `run_agent`.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Run `make check`, then `./flow issue-pr <n> "feat(agents): classify provider failures and return run output"`.**

---

### M2.T3 Evaluation runs API and queue

**Depends on:** M2.T1

**Goal:** Create, list, read and cancel evaluation runs (spec 6.4). Add the queue functions the worker uses to claim, heartbeat and finish runs (D1, D6, D7). Nothing here executes a case.

**Acceptance criteria**
- [ ] `POST /api/v1/evaluation-runs {agent_version_id, dataset_id}` → `202` with the run (`status: "pending"`).
  - Unknown version or dataset → 404.
  - A dataset with no cases, an unknown tool, an unsupported provider or invalid parameters → 422 with the reason.
  - No run is created on an error.
- [ ] `GET /api/v1/evaluation-runs` is newest first and filters on `agent_version_id`, `dataset_id` and `status`.
- [ ] `GET /api/v1/evaluation-runs/{id}` includes `total_cases` and `counts: {completed, failed, error}` (zeros included).
- [ ] `GET /api/v1/evaluation-runs/{id}/results` is ordered by `case_key` and filters on `status`. Each item has the snapshot fields, `status`, `output`, `error` and `trace_id`. 404 for an unknown run.
- [ ] `POST /api/v1/evaluation-runs/{id}/cancel`:
  - `pending` → `cancelled` with `completed_at` set.
  - `running` → `cancel_requested = true`, and the status is unchanged.
  - `completed`, `failed` or `cancelled` → 409.
  - The update is one conditional `UPDATE … WHERE status = …`, so it can't race a claim into a lost cancel.
- [ ] Queue:
  - `claim_next` returns the oldest claimable run or `None`. Claimable means `pending`, or `running` with `heartbeat_at < now() - stale_after`.
  - A pending run with `cancel_requested` is never claimed.
  - Two concurrent `claim_next` calls on separate connections never return the same run.
- [ ] `make api-types` is re-run. `docs/architecture.md`'s API table lists the routes.

**Files:**
- Create: `backend/app/schemas/evaluation_run.py`, `backend/app/services/evaluation_runs.py`, `backend/app/api/evaluation_runs.py`, `backend/app/evaluation/__init__.py`, `backend/app/evaluation/queue.py`, `backend/tests/api/test_evaluation_runs.py`, `backend/tests/evaluation/__init__.py`, `backend/tests/evaluation/test_queue.py`, `backend/tests/evaluation/conftest.py`
- Modify: `backend/app/main.py`, `backend/app/config.py`, `frontend/lib/api/*`, `docs/architecture.md`

**Interfaces:**
- Consumes: the M2.T1 models and factories. `AgentConfig.from_version` and its errors (M1). `provider_for` is **not** called at submit time: a missing key is the worker's concern (D4). The provider name is checked against a new `SUPPORTED_PROVIDERS = ("anthropic",)` in `app/agents/providers/__init__.py`, which `provider_for` also uses.
- Produces:
  - Schemas: `EvaluationRunCreate(agent_version_id: UUID, dataset_id: UUID)`, `EvaluationRunRead` (all run columns + `counts: ResultCounts`), `ResultCounts(completed: int, failed: int, error: int)`, `EvaluationResultRead` (all result columns except the M3 scoring ones, which are included but null).
  - `Settings.eval_concurrency` and `Settings.eval_stale_after_seconds` (Global Constraints).
  - `app/evaluation/queue.py`, where each function opens and commits its own transaction on the given session:
    - `async def claim_next(session: AsyncSession, *, stale_after: timedelta) -> uuid.UUID | None`: sets `status='running'`, `heartbeat_at=now()`, and `started_at` if null.
    - `async def heartbeat(session, run_id) -> bool`: returns `cancel_requested`.
    - `async def finish(session, run_id, status: Literal["completed","failed","cancelled"], error: str | None = None) -> None`: sets `completed_at`.
  - `tests/evaluation/conftest.py`: a `committed_session_factory` fixture, an `async_sessionmaker` on the test engine whose data really commits. It truncates `evaluation_results`, `evaluation_runs`, `spans`, `traces`, `test_cases`, `datasets`, `agent_versions` and `agents` (CASCADE) after each test. Queue and runner tests need real commits across connections, so the rollback-only `db_session` doesn't fit them.

- [ ] **Step 1: Write the failing API tests** in `test_evaluation_runs.py`:
  - `test_submit_returns_202_pending`, `test_submit_unknown_version_404`, `test_submit_unknown_dataset_404`, `test_submit_empty_dataset_422`, `test_submit_unknown_tool_422`. Each error test asserts that the run count is 0.
  - `test_list_filters_and_order`, `test_detail_counts_zero_filled`.
  - `test_results_ordered_by_case_key` (results inserted out of order with `make_trace`), `test_results_filter_status`, `test_results_unknown_run_404`.
  - `test_cancel_pending`, `test_cancel_running_sets_flag`, `test_cancel_finished_409` (parametrised over the three finished statuses).
- [ ] **Step 2: Write the failing queue tests** in `test_queue.py`, using `committed_session_factory`:
  - `test_claim_oldest_pending`, `test_claim_none_when_empty`.
  - `test_claim_skips_fresh_running`, `test_claim_reclaims_stale_running` (`heartbeat_at` set 120 s ago, `stale_after=60s`).
  - `test_claim_skips_cancel_requested_pending`.
  - `test_concurrent_claims_distinct`: two runs, `asyncio.gather` of two `claim_next` on two sessions → two distinct ids. With one run: one id and one `None`.
  - `test_heartbeat_returns_cancel_flag`, `test_finish_sets_completed_at`.
- [ ] **Step 3: Run** `cd backend && uv run pytest tests/api/test_evaluation_runs.py tests/evaluation -q`. Expected: FAIL.
- [ ] **Step 4: Implement the schemas, the service, the router and the queue.** Use `with_for_update(skip_locked=True)` for the claim, and the database's `now()` for every timestamp comparison.
- [ ] **Step 5: Run the tests.** Expected: PASS.
- [ ] **Step 6: Run `make api-types`, update `docs/architecture.md`, run `make check`, then `./flow issue-pr <n> "feat(api): add evaluation runs endpoints and run queue"`.**

---

### M2.T4 Evaluation runner

**Depends on:** M2.T2, M2.T3

**Goal:** Execute one claimed run (spec 6.2 steps 3–6). The runner fixes the case set, runs the cases with bounded concurrency, stores a snapshot result per case, heartbeats, honours cancel, and aborts on account-level provider failures. It resumes a reclaimed run.

**Acceptance criteria**
- [ ] On the first claim (`total_cases IS NULL`), the runner sets `total_cases` to the dataset's case count. On every claim, it runs only the cases with no result for this run. If a not-yet-run case was deleted, `total_cases` is reduced to match (D8).
- [ ] Never more than `concurrency` cases are in flight. The test measures the peak.
- [ ] Each case writes its trace and its result in one transaction, on its own session. The result copies the snapshot fields from the case as read when the case started. `output` and `error` come from `RunOutcome`.
- [ ] Outcomes:
  - `completed` → `completed`.
  - `failed` with no `provider_failure` → `failed`.
  - `provider_failure == "unavailable"` → `error`, with the trace.
  - `provider_failure == "account"` → `error`, with the trace, and the run aborts: no new cases start, in-flight ones finish, and the run ends `failed` with `error` = the outcome's error.
- [ ] An unexpected exception in a case rolls back that case's transaction and writes an `error` result with no trace and `error = f"{type(exc).__name__}: {exc}"`. The traceback is logged on `app.evaluation`, and the run continues.
- [ ] Configuration errors fail the run before any case starts, with `error` set: an unknown version tool or provider, invalid parameters, or `ProviderNotConfiguredError`.
- [ ] The heartbeat runs on a timer task every `stale_after / 3` for the whole run, so a long case can't make a live run look stale. A cancel flag seen by the heartbeat stops new cases, and the run ends `cancelled` once in-flight cases finish.
- [ ] The run ends `completed` only if every case in `total_cases` has a result.

**Files:**
- Create: `backend/app/evaluation/runner.py`, `backend/tests/evaluation/test_runner.py`

**Interfaces:**
- Consumes: `claim_next`, `heartbeat`, `finish` and `committed_session_factory` (M2.T3). `run_agent` and `RunOutcome` (M2.T2). `AgentConfig.from_version`, `ProviderFactory` (`app.api.deps`) and `ScriptedProvider` (M1).
- Produces:
  - `async def execute_run(run_id: UUID, *, session_factory: async_sessionmaker[AsyncSession], provider_factory: ProviderFactory, concurrency: int, stale_after: timedelta) -> None`
  - It never raises for a case or a provider failure. It raises only if the run row itself can't be read or finished (a bug or a lost database).

- [ ] **Step 1: Write the failing tests** in `test_runner.py`. Use `committed_session_factory`, a 3-case dataset unless stated, and a provider factory returning a fresh `ScriptedProvider` per case. Use a dict keyed by input when the outcome differs per case.
  - `test_all_cases_completed`: run `completed`, 3 results `completed`, each with `trace_id`, `output` and snapshot fields equal to the case. Each trace has `evaluation_run_id` and `test_case_id`.
  - `test_agent_failure_is_failed_result`: one case's script ends `max_tokens` → that result is `failed` with a trace, and the run is `completed`.
  - `test_unavailable_is_error_and_continues`: one case raises `ProviderAPIError(kind="unavailable")` → `error` with the trace, the other two are `completed`, and the run is `completed`.
  - `test_account_failure_aborts`: 10 cases, `concurrency=1`, and the 2nd case raises `kind="account"` → run `failed`, `error` contains the message, exactly 2 results exist.
  - `test_unexpected_exception_is_error_without_trace`: the provider factory's provider raises `RuntimeError("boom")` on one case → `error` with `trace_id is None` and `error == "RuntimeError: boom"`. Others `completed`, and no orphan trace for that case.
  - `test_concurrency_limit`: 8 cases, `concurrency=3`, and a provider that awaits an event and counts in-flight calls → peak == 3.
  - `test_resume_skips_finished_cases`: a run with `total_cases=3`, 1 existing result and a stale heartbeat → afterwards 3 results, the old one untouched, `total_cases == 3`.
  - `test_snapshot_survives_edit`: after the run, edit the case's input → the result's `case_input` is unchanged.
  - `test_cancel_during_run`: 6 cases, `concurrency=1`, and `cancel_requested` set after the first case finishes → run `cancelled` with fewer than 6 results.
  - `test_missing_key_fails_run` (factory raises `ProviderNotConfiguredError`), `test_unknown_tool_fails_run` → run `failed`, 0 results.
  - `test_deleted_unrun_case_adjusts_total`.
- [ ] **Step 2: Run** `cd backend && uv run pytest tests/evaluation/test_runner.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement `execute_run`:** an `asyncio.Semaphore(concurrency)` with a `TaskGroup` over the pending cases, a heartbeat task, and an abort/cancel `asyncio.Event` checked before each case acquires the semaphore. Each case reads its case row, ends the read transaction, and then calls `run_agent` (no transaction open during the model call). It then inserts the result and commits.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Run `make check`, then `./flow issue-pr <n> "feat(evaluation): add the evaluation runner"`.**

---

### M2.T5 Worker process

**Depends on:** M2.T4

**Goal:** `python -m app.worker` polls the queue and executes runs one at a time (D1, D5). It shuts down gracefully, and `make dev` and the Docker `full` profile run it.

**Acceptance criteria**
- [ ] The worker loop: `claim_next` → `execute_run` → repeat. When idle, it sleeps for the poll interval (default 1 s, `EVAL_POLL_INTERVAL_SECONDS`).
- [ ] An exception from `execute_run` is logged with its traceback, and the loop continues. The run stays `running` and is reclaimed after the stale timeout.
- [ ] On SIGTERM or SIGINT the worker stops claiming, lets the current run's in-flight cases finish, and exits 0. It does not mark the run finished, so it is reclaimed and resumed.
- [ ] Logs one line at start and one per run (claimed, finished with status and counts).
- [ ] `make dev-worker` exists, and `make dev` runs `dev-api`, `dev-web` and `dev-worker`. `docker-compose.yml` gains a `worker` service in the `full` profile, built from the backend image, with `command: python -m app.worker`, depending on `api` being healthy, so migrations have run if the backend image's entrypoint runs them. Check the Dockerfile; if the image doesn't migrate, depend on `db` and note it.
- [ ] An end-to-end API test: `POST` a run, call `run_once(...)` in process with a scripted provider, then `GET` the run → `completed` with counts.

**Files:**
- Create: `backend/app/worker.py`, `backend/tests/evaluation/test_worker.py`
- Modify: `Makefile`, `docker-compose.yml`, `backend/app/config.py`, `README.md`

**Interfaces:**
- Consumes: `claim_next` (M2.T3) and `execute_run` (M2.T4).
- Produces:
  - `async def run_once(*, session_factory, provider_factory, settings: Settings) -> uuid.UUID | None`: claims and executes one run, and returns its id, or `None` when idle.
  - `async def serve(*, session_factory, provider_factory, settings: Settings, stop: asyncio.Event) -> None`: the loop.
  - `def main() -> None`: builds the engine and the provider factory (`partial(provider_for, settings=...)`), installs signal handlers that set `stop`, and runs `serve`.

- [ ] **Step 1: Write the failing tests:**
  - `test_run_once_idle_returns_none`, `test_run_once_executes_oldest`.
  - `test_serve_stops_on_event` (set `stop` after one run → returns).
  - `test_serve_survives_execute_error` (a patched `execute_run` raises once → logged, the next run is still processed).
  - `test_end_to_end_via_api`, as in the acceptance criteria.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement `app/worker.py`. Update the `Makefile`, `docker-compose.yml` and README** (the worker, its settings and `make dev-worker`).
- [ ] **Step 4: Run the tests, then `make up` and `docker compose --profile full ps`.** Expected: PASS, and the `worker` container is running. Then run `make down`.
- [ ] **Step 5: Run `make check`, then `./flow issue-pr <n> "feat(evaluation): add the worker process"`.**

---

### M2.T6 `make eval` and milestone docs

**Depends on:** M2.T5, M2.T7

**Goal:** An author-only command that proves the exit criterion against the real API (spec 6.6), and the docs that describe the evaluation flow.

**Acceptance criteria**
- [ ] `make eval` (`cd backend && uv run python -m app.eval`):
  - finds `weather-bot` v1 and the `weather-basics` dataset through the API in process (like `app/smoke.py`),
  - submits a run, then calls `run_once` in process until the run finishes,
  - prints one line per result (`case_key`, status, `trace_id` and the first 80 characters of `output` or `error`), then the counts.
- [ ] Exit codes: 0 if the run is `completed` and no result is `error`, 1 otherwise, and 2 if `ANTHROPIC_API_KEY` is missing (checked before submitting).
- [ ] Tested with a scripted provider factory, never the real API: `test_eval_ok`, `test_eval_error_result_exits_1`, `test_eval_no_key_exits_2`.
- [ ] `docs/architecture.md` gains an "Evaluation runs" section (spec 6.2's flow, the worker in the process diagram, and the result statuses). The README lists `make eval` as author-only, with its approximate cost (about 50–75 model calls).

**Files:**
- Create: `backend/app/eval.py`, `backend/tests/test_eval.py`
- Modify: `Makefile`, `docs/architecture.md`, `README.md`

**Interfaces:**
- Consumes: `run_once` (M2.T5) and the evaluation runs API (M2.T3). The `_ok`/`_find_agent_id` pattern from `app/smoke.py`: move the shared helpers into `app/cli_support.py` rather than copying them.
- Produces: `async def evaluate(settings: Settings, out: TextIO, *, provider_factory: ProviderFactory | None = None) -> int` (the exit code). `main()` calls it with the real factory.

- [ ] **Step 1: Write the three failing tests. Run them.** Expected: FAIL.
- [ ] **Step 2: Implement `app/eval.py` and the Makefile target.** Do not run it with a real key.
- [ ] **Step 3: Run the tests.** Expected: PASS.
- [ ] **Step 4: Update the docs. Run `make check`, then `./flow issue-pr <n> "feat(evaluation): add make eval and document evaluation runs"`.**

---

### M2.T7 Seed dataset: about 25 weather-basics cases

**Depends on:** none

**Goal:** Grow `weather-basics` in `datasets/sample.json` from 3 to about 25 cases (D11). They are the material for M3's scores and M5's regression demo, so the author reviews them in the PR.

**Acceptance criteria**
- [ ] `weather-basics` has 24–28 cases with keys `case_001`… in order. The 3 existing cases keep their keys and content.
- [ ] Coverage, each with at least 3 cases:
  - single-city lookups
  - multi-city questions (comparisons, needing two tool calls)
  - an unknown or fictional city (the tool's behaviour for unknown locations decides the expectation)
  - ambiguous requests (no location given, or an ambiguous name like "Springfield")
  - questions answerable without a tool, with `expected_output` where there is one right answer
  - off-topic or refused requests
- [ ] Every case has 1–4 `expected_behavior` strings, each checkable from the trace or the answer (for example "calls get_weather with location Paris", not "is helpful"). Tags are drawn from a small fixed set listed in the PR summary.
- [ ] `make seed` on a database seeded with the old 3 cases adds the new cases and reports them as created. A test asserts the dataset loads with `TestCaseCreate` validation and has unique keys.
- [ ] The PR summary has a table of every case (key, input, tags), so the author can review the cases without opening the JSON.

**Files:**
- Modify: `datasets/sample.json`, `backend/tests/test_seed.py`

- [ ] **Step 1: Read `app/agents/tools.py`** to learn what `get_weather` returns for known and unknown locations, so the expectations match the tool.
- [ ] **Step 2: Write the failing test** `test_seed.py::test_sample_dataset_shape`: the file parses, `weather-basics` has 24–28 cases, keys are unique and sequential, and every category tag appears at least 3 times. Run it. Expected: FAIL.
- [ ] **Step 3: Write the cases.** Run the test and `test_seed_is_idempotent`. Expected: PASS.
- [ ] **Step 4: Run `make check`, then `./flow issue-pr <n> "feat(datasets): grow weather-basics to 25 cases"` with the case table in `--summary`.**

---

### M2.T8 Test factories, empty-key and key-rename coverage

**Depends on:** M2.T1

**Goal:** Close #51 and #25: the missing end-to-end empty-key test, shared factories in place of inline builders, and the 409 on renaming a test-case key to an existing key.

**Acceptance criteria**
- [ ] `test_runs.py::test_empty_key_503`: with `anthropic_api_key=""` and the real `provider_for`, `POST /api/v1/runs` → 503 `ANTHROPIC_API_KEY is not set`, and the trace count is 0.
- [ ] Inline `Trace` builders in the dataset tests and the Agent/AgentVersion builders in `tests/models/test_constraints.py` use `tests/factories.py` (`make_trace` from M2.T1, `make_agent_version`). Where a factory doesn't fit, a one-line comment says why.
- [ ] `test_datasets.py::test_rename_case_key_to_existing_409`: `PATCH` a case's key to another case's key in the same dataset → 409. If the service returns 500 today, fix the mapping of `uq_test_cases_dataset_id_key` to `ConflictError`.
- [ ] The test count doesn't drop.

**Files:**
- Modify: `backend/tests/api/test_runs.py`, `backend/tests/api/test_datasets.py`, `backend/tests/models/test_constraints.py`, `backend/app/services/datasets.py` (only if the 409 test fails)

- [ ] **Step 1: Write the two new tests. Run them.** Expected: `test_empty_key_503` PASS or FAIL (either way it's the new coverage). `test_rename_case_key_to_existing_409` PASS if the mapping exists, otherwise FAIL.
- [ ] **Step 2: Fix any failure. Refactor the tests onto the factories.** Run `cd backend && uv run pytest -q`. Expected: PASS, with a count ≥ before + 2.
- [ ] **Step 3: Run `make check`, then `./flow issue-pr <n> "test: share factories, cover empty key and key-rename conflict"`.** Note in `--summary` that it also closes #25.

---

### M2.T9 Agent adapter, runtime and settings hardening

**Depends on:** M2.T2

**Goal:** Close #47: mask secrets in `Settings`, declare `httpx2`, make `make record` fail cleanly, and fix the spec and plan wording.

**Acceptance criteria**
- [ ] `Settings.anthropic_api_key: SecretStr | None`.
  - `test_settings_masks_key`: a fake key appears in none of `repr(Settings())`, `str(Settings())` or `Settings().model_dump_json()`.
  - `test_provider_for_uses_real_key`: the client is built with the raw value.
  - `.get_secret_value()` is called only in `provider_for`, `record.py`, `smoke.py` and `eval.py` (if M2.T6 has merged; otherwise `eval.py` does it when it lands, and the PR notes it).
  - An empty string still counts as "not set".
- [ ] `database_url` and `test_database_url` are left as `str`. Record the reason in the PR notes and in a comment on `Settings`: they're passed to SQLAlchemy and Alembic in many places, and SQLAlchemy already masks passwords in `URL` repr.
- [ ] `httpx2` is a declared dependency in `backend/pyproject.toml`.
- [ ] `make record` with an invalid key prints one line (`record: anthropic API error: <describe>`), exits 1 and writes nothing. Tested with a fake client raising `AuthenticationError`.
- [ ] Spec M1 D7 lists "`tool_use` with no tool calls → failed: model stopped for tool_use without calling a tool". M1.T1's acceptance criterion in the M1 plan is corrected to "passes an unknown stop reason through (D7)".
- [ ] The remaining two bullets of #47 are resolved in the PR notes:
  - Partial-text `max_tokens` re-record: deferred to the author (it needs `make record`). The PR notes say so, and the Orchestrator records it in the milestone PR.
  - Earlier tool results when a later tool call fails: decided as "not carried". The run has failed, the results are on their spans, and the conversation state is discarded. Add a one-line comment in `graph.py`.

**Files:**
- Modify: `backend/app/config.py`, `backend/app/agents/providers/__init__.py`, `backend/app/agents/providers/record.py`, `backend/app/smoke.py`, `backend/app/agents/graph.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/tests/agents/test_provider_for.py`, `backend/tests/agents/test_record.py`, `backend/tests/test_config.py` (create if missing), `docs/specs/2026-11-19-m1-agent-execution-tracing-design.md`, `docs/plans/2026-11-19-m1-agent-execution-tracing.md`

- [ ] **Step 1: Write the failing tests** named above. Run them. Expected: FAIL.
- [ ] **Step 2: Implement.** Run `cd backend && uv run pytest -q`. Expected: PASS.
- [ ] **Step 3: Edit the spec and plan wording. Run `make check`, then `./flow issue-pr <n> "fix(agents): mask secrets in settings and harden make record"`** with `--notes` covering the deferred re-record and the `database_url` decision.

---

### M2.T10 flow follow-ups from the M1 reviews

**Depends on:** none

**Goal:** Close #46.

**Acceptance criteria**
- [ ] Re-running `milestone-start` keeps ticked checkboxes on existing issues. `test_milestone.py::test_restart_preserves_ticked_boxes` uses a fake gh whose issue body has `- [x]` on a line whose text matches the plan's criterion.
- [ ] `issue-pr --notes` strips a leading "Deviations" heading wherever the first non-blank line is, with a test.
- [ ] `test_cli.py::test_review_context_reads_plan_from_pr_base` asserts that the PR's base branch reaches the plan reader.
- [ ] `issue-pr` builds the task link from `origin/<base>`, not the working tree, with a test.
- [ ] The cross-milestone dependency read (`issue-start`) and `issue-merge` not waiting for non-`ci` checks are documented as intended in `CONTRIBUTING.md`, with a sentence each on why. Change the code instead if a simple fix exists, and say which in the PR.

**Files:**
- Modify: `scripts/flow/flow/{cli,milestone,plan}.py`, `scripts/flow/tests/{test_cli,test_milestone,test_plan}.py`, `CONTRIBUTING.md`

- [ ] **Step 1: Write the failing tests. Run `cd scripts/flow && uv run pytest -q`.** Expected: FAIL.
- [ ] **Step 2: Implement.** Run the tests. Expected: PASS.
- [ ] **Step 3: Update `CONTRIBUTING.md`. Run `make check`, then `./flow issue-pr <n> "fix(flow): keep ticked boxes and read task links from base"`.**

---

### M2.T11 Adapter: parse and replay thinking blocks

**Depends on:** none
**Checkpoint**

**Goal:** Fix the bug that the author's M2 `make eval` run found (run `648a1255`: 5 of 26 cases failed with `unknown content block type 'thinking'`). On `claude-sonnet-5-5`, adaptive thinking is on whenever the request omits `thinking`, and the model sometimes returns `thinking` blocks. The M1 adapter accepts only `text` and `tool_use`. In a tool loop, thinking blocks must be sent back unchanged in the assistant turn, in their original order. This is a checkpoint because the author records the new fixture with `make record` (paid) before it merges, as in M1.T1.

**Acceptance criteria**
- [ ] `parse_message` accepts `thinking` and `redacted_thinking` blocks. They never become part of `ModelResponse.text`. Any other unknown block type still raises `ProviderResponseError` (M1 D3).
- [ ] `ModelResponse` and `AssistantTurn` carry the assistant content exactly as the API returned it: `raw_content: tuple[dict[str, Any], ...]`, every block including thinking with its `signature`, in order. `to_anthropic_kwargs` sends an `AssistantTurn` that has `raw_content` back verbatim. Turns built without it (tests, older code) are rebuilt from `text` and `tool_calls` as today.
- [ ] The `llm` span records `agentops.llm.thinking_blocks` (int), the count of thinking and redacted blocks. Thinking text is not stored, since the default display is omitted anyway.
- [ ] A runtime test with `ScriptedProvider` covers the replay. A `tool_use` response carrying a thinking block, then a final answer: the second request's assistant turn contains the thinking block unchanged, before the `tool_use` block. The run is `completed`.
- [ ] Adapter replay tests:
  - a recorded response with a thinking block, then tool use, parses to the right `tool_calls` with `raw_content` intact
  - a `redacted_thinking` block parses
  - an unknown block type (for example `"mystery"`) still raises
- [ ] `RECORDABLE_SCENARIOS` gains `thinking_tool_use`. `make record` retries that scenario (up to 5 attempts) until the response contains a thinking block, and fails clearly if none appears. The fixture is synthetic until the author records it. The author runs `make record` on the PR branch and commits the recorded fixture before approving. `test_recordable_fixtures_are_recorded` must then pass.
- [ ] After merge, the author re-runs `make eval`. No result fails with `unknown content block type`.
- [ ] The author approves the PR (`checkpoint-approved` label on the issue) before it merges.

**Files:**
- Modify: `backend/app/agents/providers/base.py`, `backend/app/agents/providers/anthropic.py`, `backend/app/agents/providers/record.py`, `backend/app/agents/graph.py`, `backend/tests/agents/test_anthropic_provider.py`, `backend/tests/agents/test_runtime.py`, `backend/tests/agents/test_record.py`, `backend/tests/agents/test_fixture_guard.py`, `backend/tests/agents/fakes.py`
- Create: `backend/tests/agents/fixtures/anthropic/thinking_tool_use.json` (synthetic, then recorded by the author)

---

## Milestone finish (Orchestrator)

1. On `milestone/m2`, run `make setup db migrate seed`, then `make check`.
2. Ask the author to run `make eval` (author-only: paid API calls with the key from their Keychain) and paste its output. That output is the exit-criteria evidence in the milestone PR. While waiting, run `make up` without a key, submit a run to the `api` container with `curl`, and confirm the `worker` container ends it `failed` with `ANTHROPIC_API_KEY is not set`. That proves the containers' queue path without paid calls. Run `make down` afterwards.
3. Run `./flow milestone-pr 2`, and fill in the Summary, Verification, Deviations and Review guide. Tick the exit criterion: "A developer can submit a dataset and execute it against an agent, producing a stored result for every test case". List the deferred `max_tokens` re-record (#47) as open author work.
4. Create backlog issues for the deferred minor findings. Mark the PR ready and notify the author.
