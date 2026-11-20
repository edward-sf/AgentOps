# M2 — Evaluation Engine: Design

- **Date:** 2026-11-19
- **Milestone:** [M2 - Evaluation engine](https://github.com/edward-sf/AgentOps/milestone/3)
- **Status:** Approved (2026-11-19)

## 1. Intent

AgentOps is a solo-developer portfolio project: an evaluation and observability platform for AI agents (see `BRIEF.md`). Its centrepiece, in M5, is detecting a regression after a model or prompt change and pointing at the failed cases. M1 built the run primitive: `run_agent()` runs one input through an AgentVersion and stores a trace.

M2 turns that primitive into repeatable evaluations. You submit a dataset to run against an AgentVersion, and AgentOps runs every test case and stores one result per case, each linked to its trace. Later milestones build on those results:

- M3 scores each result (pass/fail, accuracy, relevance, instruction adherence, tool correctness).
- M4 launches evaluations from the UI and drills down from a run to a failed case and its trace.
- M5 compares two runs case by case to find regressions.

## 2. Goals, non-goals, anti-goals

**Goals (milestone exit criteria)**
- `POST /api/v1/evaluation-runs` submits a dataset against an AgentVersion. A separate worker process executes it, and every test case gets a stored result linked to its trace.
- A run's status and per-case results can be read back through the API while the run is in progress and after it finishes.
- The seeded `weather-basics` dataset grows to about 25 cases (decision 11).
- `make eval` shows this end to end against the real Anthropic API on the seeded `weather-basics` dataset. Its output is the milestone's verification evidence.
- The `ci` gate stays fast, deterministic and keyless (M1 decision 3).
- The backlog already assigned to M2 is done: [#25](https://github.com/edward-sf/AgentOps/issues/25), [#46](https://github.com/edward-sf/AgentOps/issues/46), [#47](https://github.com/edward-sf/AgentOps/issues/47), [#51](https://github.com/edward-sf/AgentOps/issues/51).

**Non-goals (later milestones)**
- Scoring of any kind, including `passed`, `score`, `scores` and `explanation` (M3). M2 leaves them empty.
- Any UI. The frontend changes only through regenerated API types (M4).
- Comparing runs and detecting regressions (M5).
- Metrics rows, cost and latency aggregates (M6).
- Running a case more than once per run to measure consistency. The `(run, test_case)` unique constraint stays. Revisit when consistency scoring is designed.
- An external queue (Redis, SQS, Celery). Postgres is the queue (decision 1).
- Dataset versioning or locking (decision 2 chose snapshots).
- Auth and multi-tenancy (not planned).

**Anti-goals (would make M2 a failure even if it "works")**
- A run that is lost or stuck forever when the API or worker restarts.
- A run that quietly ends `completed` with cases missing a result.
- Results that can't be told apart from a case being edited after the run (decision 2).
- One case's agent failure aborting the rest of the run.
- A provider outage (bad key, no credits, overload) recorded as the agent answering badly, so that M3 scores it and M5 reports it as a regression.
- A CI gate that calls a model API.
- Unbounded concurrency that floods the provider and turns rate limits into failed cases.

## 3. Constraints

- The stack is fixed by the brief, M0 and M1: Python 3.12, FastAPI, async SQLAlchemy with psycopg 3, Alembic, PostgreSQL 17, LangGraph, OpenTelemetry. Claude is the model provider.
- M0 conventions apply: routes under `/api/v1`, paginated lists `{items, total, limit, offset}`, errors `{"detail": …}`, enums as `text` plus `CHECK`, tests against real Postgres, committed generated frontend types.
- M1 decisions stand: OTel spans are the boundary between running and storing (D2), CI never calls a model API (D3), and a failed agent run is data, not an exception (6.2).
- `ANTHROPIC_API_KEY` exists only on the author's machine. Paid commands (`make record`, `make smoke`, `make eval`) are run only by the author.
- The workflow is fixed by `CONTRIBUTING.md` and `./flow`.

## 4. Decisions

1. **Runs execute in a separate worker process that uses the `evaluation_runs` table as its queue** (author decision, 2026-11-19).
   - The API only creates a `pending` run and returns 202. It never executes cases.
   - A worker (`python -m app.worker`) polls for work. It claims a run with `SELECT … FOR UPDATE SKIP LOCKED`, so each run goes to exactly one worker, and more than one worker may run safely.
   - No new infrastructure. Runs survive an API restart, and the shape matches how M7 deploys (an API service and a worker service).
2. **Each result snapshots the test case as it was when it ran** (author decision, 2026-11-19).
   - Test cases stay editable. A result stores copies of the case's `key`, `input`, `expected_behavior`, `expected_output` and `tags` at run time.
   - M3 scores against the snapshot, not the live case. M5 can see that a case changed between two runs and report that instead of calling it a regression.
3. **A result records what happened before M3 says whether it was good** (Claude's call).
   - New `evaluation_results.status`:
     - `completed`: the agent answered. Trace stored.
     - `failed`: the agent's own run failed, for example the step limit, a tool exception, or a malformed provider response (M1's loud-failure rule). Trace stored. M3 scores this as a failure of the agent.
     - `error`: the result says nothing about the agent. Either the provider failed (decision 10, trace stored) or our own code raised (no trace). `error` holds the message. M3 never scores `error` results.
   - The result also stores the agent's final answer as `output` (copied from the trace), so M3, M4 and `make eval` read one row (Claude's call, from the builder check; the milestone asks for "expected and actual results").
   - `passed` becomes nullable. M2 leaves it, `score`, `scores` and `explanation` empty.
4. **One case's outcome never stops the run** (Claude's call). A `failed` or `error` case is stored and the worker moves on. The run ends `completed` once every case has a result, whatever their statuses. A run ends `failed` only when it can't proceed at all: its AgentVersion names a provider or tool that no longer resolves, `ANTHROPIC_API_KEY` is missing in the worker, or the provider rejects the account (decision 10). `evaluation_runs.error` then says why. A `failed` run may have cases without a result; a `completed` run never does.
5. **Bounded concurrency per run** (Claude's call). A worker executes one run at a time, and runs up to `EVAL_CONCURRENCY` of its cases at once (setting, default 4). Total load is therefore workers × `EVAL_CONCURRENCY`; one worker is the default everywhere, and a global cap is out of scope. Each case uses its own DB session and commits its trace and result together, so a crash never leaves a trace without its result.
6. **A run that a dead worker abandoned is reclaimed, and it resumes** (Claude's call).
   - A worker updates the run's `heartbeat_at` while it works. A `running` run whose heartbeat is older than `EVAL_STALE_AFTER` (setting, default 60 s) can be claimed by another worker.
   - Resuming skips cases that already have a result. The `(run, test_case)` unique constraint makes a duplicate result impossible.
7. **Cancellation is cooperative** (Claude's call). `POST /api/v1/evaluation-runs/{id}/cancel` moves a `pending` run straight to `cancelled`. A `running` run is marked for cancellation. The worker starts no new cases, lets in-flight cases finish and store their results, then sets `cancelled`. Cancelling a finished run is a 409.
8. **The case set is fixed when the run starts executing** (Claude's call). The worker reads the dataset's cases when it first claims the run and records the count as `evaluation_runs.total_cases`. Cases added to the dataset later aren't part of that run. A reclaimed run resumes with the cases that still exist and lack a result. A case deleted mid-run can't happen: deletion is refused while a result or trace references it, and a not-yet-run case that is deleted is simply skipped and the run's total is corrected.

9. **Results are written once** (Claude's call). M2 never updates a result after it is stored, except that M3 will fill in the scoring columns.
10. **Provider-side failures are not agent failures** (author decision, 2026-11-19).
    - The runtime's outcome distinguishes a provider failure from an agent failure. Provider failures are errors from the provider's API itself after the adapter's retries: authentication or permission (401/403), billing or credit exhausted, rate limit or overload (429/529) and server errors (5xx), and connection or timeout errors. A malformed response stays an agent `failed` (M1 decision 3).
    - The trace is still stored (M1: a failed run is data), and the result is `error` with the trace linked.
    - Authentication, permission and billing errors **abort the run**: the worker starts no new cases, lets in-flight ones finish, and ends the run `failed` with the provider's message. Every other case would fail the same way and cost money doing it.
    - Rate-limit, overload, server and connection errors record that case as `error` and the run continues. M2 doesn't retry them at the run level.
    - `make eval` exits non-zero if the run isn't `completed` or any result is `error`.
11. **The seed dataset grows to about 25 cases** (author decision, 2026-11-19). `weather-basics` in `datasets/sample.json` covers single-city lookups, multi-city questions, an unknown city, ambiguous requests, questions needing no tool, and off-topic or refused requests. It uses only the existing `get_weather` tool, so no agent changes. Each case has `expected_behavior`, and `expected_output` where there is one right answer. A real `make eval` costs roughly 50–75 model calls.

## 5. Approaches considered (how runs execute)

| Option | Summary | Trade-off |
| ------ | ------- | --------- |
| A. Background task in the API process | `POST` returns 202 and an asyncio task runs the cases. | Simplest. A restart drops in-progress runs, which then need marking failed on startup. Ties eval load to API latency. |
| **B. Separate worker, Postgres as the queue (chosen)** | `evaluation_runs` rows are the queue; a worker claims them with `SKIP LOCKED`. | One more process in `make dev` and Docker. Survives restarts, scales to more workers, matches M7's deployment. |
| C. Synchronous, like M1 | One request blocks until the dataset finishes. | Fine for 3 cases; unusable for 50 and for M4's launch button. |

**Chosen: B** (author decision). Slightly more work than A, and it holds up through M4 and M7.

**Case snapshots vs dataset versioning:** versioning or locking datasets after their first run gives cleaner comparisons but is stricter for the author and needs more API work now. Snapshots keep editing easy and still let M5 detect a changed case. **Chosen: snapshots** (author decision).

## 6. Architecture

### 6.1 Modules

```
backend/app/
  evaluation/
    runner.py       # executes one claimed run: fan out cases, bounded concurrency, heartbeat, cancel
    queue.py        # claim / heartbeat / finish a run (SKIP LOCKED, stale reclaim)
  worker.py         # `python -m app.worker`: poll loop, signal handling, graceful shutdown
  api/evaluation_runs.py, services/evaluation_runs.py, schemas/evaluation_run.py
```

`run_agent()` gains optional `evaluation_run_id` and `test_case_id`. It sets them as root-span attributes (new `agentops.evaluation_run_id` and `agentops.test_case_id`), and ingest copies them onto the Trace. That keeps M1 decision 2: what links a trace to an evaluation travels as OTel attributes, so an external agent could report the same later.

### 6.2 A run, step by step

1. `POST /api/v1/evaluation-runs {agent_version_id, dataset_id}`. Errors: 404 if either doesn't exist, 422 if the dataset has no test cases or the AgentVersion names an unknown tool or unsupported provider. Otherwise it creates a `pending` run and returns `202` with the run.
2. A worker claims the oldest claimable run (`pending`, or `running` with a stale heartbeat), sets `running`, `started_at` (first claim only) and `heartbeat_at`, and commits.
3. The runner resolves the AgentVersion and provider. If that fails, the run ends `failed` with `error` (decision 4).
4. It loads the cases without a result for this run, records `total_cases` on the first claim, and runs them with bounded concurrency. Each case:
   - snapshots the case,
   - calls `run_agent(..., evaluation_run_id, test_case_id)`,
   - writes the result (`completed` or `failed`, with `trace_id`) in the same transaction as the trace,
   - on a provider failure, writes an `error` result with the trace, and on an account-level failure requests an abort (decision 10),
   - on an unexpected exception, rolls back, then writes an `error` result with no trace and the exception message, and logs the traceback.
5. Between cases it refreshes the heartbeat and checks for a cancel request (decision 7).
6. When every case has a result, the run becomes `completed` with `completed_at`.
7. On SIGTERM/SIGINT the worker stops claiming, lets in-flight cases finish, and exits. The run stays `running` and is reclaimed by the next worker after the stale timeout (or immediately by the same worker on restart, if the builder records ownership).

### 6.3 Data model changes (migration `0003`)

- `evaluation_runs`: add `total_cases` (int, nullable until first claim), `heartbeat_at` (timestamptz, nullable), `cancel_requested` (bool, default false). Index to make the claim query cheap (status plus `created_at`).
- `evaluation_results`:
  - add `status` (`completed` | `failed` | `error`, `CHECK`), `error` (text, nullable), `output` (text, nullable),
  - add snapshot columns `case_key`, `case_input`, `case_expected_behavior` (JSONB), `case_expected_output`, `case_tags` (JSONB),
  - make `passed` nullable,
  - add a `CHECK` that `trace_id` is set when status is `completed` or `failed`. It is optional for `error` (decision 3).
- `traces.evaluation_run_id` and `traces.test_case_id` already exist (M0).
- Migration round-trip test (upgrade, downgrade, upgrade) still passes.
- `docs/architecture.md` is updated: ERD, statuses, API table, and the worker in the process diagram.

### 6.4 API

| Method and path | Purpose |
| --------------- | ------- |
| `POST /api/v1/evaluation-runs` | Body `{agent_version_id, dataset_id}`. Returns `202` with the run. Errors as in 6.2 step 1. |
| `GET /api/v1/evaluation-runs` | Newest first. Filters: `agent_version_id`, `dataset_id`, `status`. Standard pagination. |
| `GET /api/v1/evaluation-runs/{id}` | The run with progress counts: `total_cases`, and result counts by status. 404 if not found. |
| `GET /api/v1/evaluation-runs/{id}/results` | The run's results with their snapshots and `trace_id`, ordered by `case_key`. Filter: `status`. Standard pagination. |
| `POST /api/v1/evaluation-runs/{id}/cancel` | Decision 7. Returns the run. 409 if already finished. |

The cancel route follows the existing action style if one is already set (the datasets API uses `test-cases:bulk`). The builder matches it and notes the choice.

`make api-types` regenerates the frontend types. No UI is added.

### 6.5 Testing

- **Queue:** claim order, `SKIP LOCKED` (two concurrent claimers never get the same run), stale reclaim, a fresh heartbeat is never reclaimed.
- **Runner:** with M1's scripted fake provider. Every case gets a result. A failed agent run gives a `failed` result with a trace. A provider 429 gives an `error` result with a trace and the run continues; a 401 aborts the run as `failed`. An injected exception gives an `error` result, no trace, and the rest of the run continues. Resume after a simulated crash skips finished cases. Concurrency never exceeds the limit. Cancel before and during a run. Snapshots survive a later edit of the case.
- **API:** httpx against real Postgres, as in M0 and M1. The worker isn't needed for API tests; one end-to-end test drives the runner in process after the POST.
- No test calls a model API.

### 6.6 Processes and Make targets

- `make dev-worker` runs the worker with reload; `make dev` runs API, web and worker.
- The Docker `full` profile gains a `worker` service built from the backend image.
- `make eval`: author-only, like `make smoke`. Submits `weather-basics` against `weather-bot` v1 through the API in process, runs the runner in process until the run finishes, and prints one line per case (key, status, trace id, final answer). Exits non-zero if the run doesn't end `completed`, and with a clear message if `ANTHROPIC_API_KEY` is missing.

## 7. Workflow notes for the plan

- **Checkpoint:** the task that adds migration `0003` and the result/run schema (decisions 2, 3, 6). It fixes the result shape M3, M4 and M5 all read. Tasks depending on it wait for `checkpoint-approved`.
- The ~25-case dataset (decision 11) is its own task. The author reviews the cases in the PR, since they are the material for M3's scores and M5's demo.
- The backlog issues [#25](https://github.com/edward-sf/AgentOps/issues/25), [#46](https://github.com/edward-sf/AgentOps/issues/46), [#47](https://github.com/edward-sf/AgentOps/issues/47) and [#51](https://github.com/edward-sf/AgentOps/issues/51) become plan tasks, so their issues are reconciled to plan task IDs. #47's re-record of `max_tokens.json` is an author-only step; its task may land without it if the issue notes it as deferred.

## 8. Left to the builder

- The worker's poll interval and backoff when idle, and shutdown timeouts.
- Whether heartbeats run on a timer or between cases, as long as a long case can't make a live run look stale (the stale timeout must exceed the longest expected case, or the heartbeat must be timer-based).
- Exact column names, index definitions, and the response schema field names, within section 6.
- The `make eval` output format.
- How the plan splits the work into tasks, apart from the checkpoint in section 7.
