# M1 — Agent Execution and Tracing: Design

- **Date:** 2026-11-19
- **Milestone:** [M1 - Agent execution and tracing](https://github.com/edward-sf/AgentOps/milestone/2)
- **Status:** Approved (2026-11-19)

## 1. Intent

AgentOps is a solo-developer portfolio project: an evaluation and observability platform for AI agents (see `BRIEF.md`). Its centrepiece, in M5, is detecting a regression after a model or prompt change and pointing at the failed cases. Everything after M1 stands on traces:

- M2 runs datasets against an agent and needs a run primitive.
- M3 scores tool correctness from the recorded tool calls.
- M4 shows traces in a viewer.
- M6 derives latency, token and cost metrics from them.

M1 builds that primitive. AgentOps runs an agent built with LangGraph on one input, records every step as OpenTelemetry spans, and stores them as Trace and Span rows in Postgres. Afterwards, the run can be rebuilt from the database alone.

## 2. Goals, non-goals, anti-goals

**Goals (milestone exit criteria)**
- An AgentVersion (for example the seeded `weather-bot`) runs on an input through `POST /api/v1/runs` and completes, including tool calls.
- `GET /api/v1/traces/{id}` rebuilds that run from stored rows: the trace with its input, output and status, and a span tree of LLM calls and tool calls with their inputs, outputs, timing, token counts and errors.
- `make smoke` shows this end to end against the real Anthropic API, and its output is the milestone's verification evidence.
- The `ci` gate stays fast, deterministic and keyless.
- The deferred chores already assigned to M1 are done: [#22](https://github.com/edward-sf/AgentOps/issues/22), [#24](https://github.com/edward-sf/AgentOps/issues/24), [#29](https://github.com/edward-sf/AgentOps/issues/29). [#23](https://github.com/edward-sf/AgentOps/issues/23) is resolved by decision 1 (section 4).

**Non-goals (later milestones)**
- Running datasets, evaluation runs, background execution or job queues (M2).
- Scoring (M3).
- Any UI. The trace viewer is M4. The frontend changes only through regenerated API types.
- Rows in the `metrics` table and cost calculation (M6). M1 records raw token counts and timing on spans.
- OpenAI support (later, behind the provider interface).
- Receiving traces from agents hosted elsewhere through OTLP. Prerequisites are tracked as [#31](https://github.com/edward-sf/AgentOps/issues/31) and [#32](https://github.com/edward-sf/AgentOps/issues/32) in M6.
- Auth and multi-tenancy (not planned).

**Anti-goals (would make M1 a failure even if it "works")**
- A CI gate that calls a model API, or that turns red because of something outside our code.
- Traces that quietly store half-parsed or wrong data when a provider response is unexpected.
- A noisy span tree full of framework internals that M4 would have to filter out.
- A trace design that only works for our own runtime, so that external ingestion later means rewriting storage.
- An agent that loops forever without that showing up in its trace.

## 3. Constraints

- The stack is fixed by the brief and M0: Python 3.12, FastAPI, async SQLAlchemy with psycopg 3, Alembic, PostgreSQL 17, LangGraph, OpenTelemetry. Claude is the model provider.
- M0 conventions apply. Routes live under `/api/v1`. Lists are paginated `{items, total, limit, offset}`. Errors are `{"detail": …}` with 404, 409 and 422. Enums are `text` plus `CHECK`. Tests run against real Postgres. Generated frontend API types are committed and checked by CI.
- The workflow is fixed by `CONTRIBUTING.md` and `./flow`: TDD, `make check`, a fresh review for each issue PR, and checkpoints approved by the author.
- `ANTHROPIC_API_KEY` exists only on the author's machine. CI has no key and never will (decision 3).

## 4. Decisions

1. **Spans link to their parent by OTel span ID, not by row ID** (author approved, 2026-11-19; resolves [#23](https://github.com/edward-sf/AgentOps/issues/23)).
   - OpenTelemetry exports a span when it ends, and a parent ends after its children, so a child arrives before its parent's row exists. A foreign key from `spans.parent_span_id` to `spans.id` would reject those inserts.
   - Instead, a span stores its parent's `otel_span_id` (unique per trace), with no foreign key. The parent is resolved by `(trace_id, parent otel span id)` when read.
   - This works for M1's in-process path and for out-of-order OTLP batches if external ingestion is added later.
2. **OpenTelemetry is the boundary between running agents and storing traces** (author approved, 2026-11-19).
   - The runtime emits standard OTel spans.
   - One ingest service turns finished OTel spans into Trace and Span rows. It accepts spans in any order and is idempotent on `(otel_trace_id, otel_span_id)`.
   - M1 feeds it in process. An OTLP receiver could feed the same service later ([#31](https://github.com/edward-sf/AgentOps/issues/31), [#32](https://github.com/edward-sf/AgentOps/issues/32)).
3. **CI never calls a model API** (author decision, 2026-11-19). The `ci` gate checks only our own code; nothing external can block a merge.
   - **Gate tests** use a scripted fake model for runtime and tracing logic. The provider adapter is tested against **recorded real responses**, which are fixtures replayed without a key. This keeps adapter tests deterministic while still using real response shapes.
   - **CI catches drift that we cause.** Each fixture records the SDK version and the model ID it was recorded with. A gate test fails when the locked SDK version or the model IDs in use (seed data, tests) no longer match, and its message says to re-record. Re-recording (`make record`) is a deliberate local step that needs the key. Its fixture diff is reviewed with the PR.
   - **Drift that the provider causes fails loudly at runtime.** The adapter parses responses into strict types. Anything unexpected becomes an error span with a clear message and a `failed` trace. It is never stored as a half-parsed trace that looks fine. Real runs surface it: `make smoke`, M2 evaluations and the demo.
   - **`make smoke`** runs one real agent task end to end, through `POST /api/v1/runs` and `GET /api/v1/traces/{id}`, and prints the rebuilt trace tree. It is manual and needs the key. The Orchestrator runs it at milestone verification.
   - A scheduled, non-blocking drift check is out of scope. If wanted, it belongs with monitoring (M6).
4. **Runs are synchronous in M1** (author decision, 2026-11-19). `POST /api/v1/runs` blocks until the agent finishes and all spans are stored, then returns. Background execution is M2's decision, made with M2's requirements in hand.
5. **Agent defaults** (proposed by Claude, accepted by the author, 2026-11-19):
   - **One generic tool-calling graph.** A model node and a tools node loop until the model answers. The graph is built from an AgentVersion's provider, model, system prompt, parameters and tool list. Tools come from a registry in code, looked up by name. A new configuration is a new version, not new code.
   - **`get_weather` returns fixed, made-up weather** and calls no external service, so differences between runs come from the agent and not the weather.
   - **A step limit,** read from `parameters.max_steps` (default 10). Hitting it marks the trace `failed` with a clear reason.
   - **Token counts and timing are recorded on LLM spans** as attributes, so M6 can derive metrics from existing traces.
   - **Anthropic only,** behind a provider interface.
6. **Our own instrumentation on the plain Anthropic SDK** (author decision, 2026-11-19; see section 5). LangGraph orchestrates. LangChain's model wrappers and third-party instrumentors are not used.

7. **Run outcome by stop reason** (Claude's call, 2026-11-19). `failed` means "the run did not produce a usable answer". A poor answer is still `completed`; judging it is M3's job.
   - `tool_use`: the loop continues.
   - `tool_use` with no tool calls → `failed`: "model stopped for tool_use without calling a tool".
   - `end_turn` and `stop_sequence`: `completed`.
   - `refusal`: `completed`, with the refusal as output and the finish reason on the span. It is an answer, and M3 scores it.
   - `max_tokens`: `failed` ("answer truncated at max_tokens"). A cut-off answer must not look like a real one.
   - Any other stop reason (for example `pause_turn`, which only server tools produce and M1 doesn't use): `failed` as unexpected, per decision 3.
8. **`max_steps` counts model calls** (Claude's call, 2026-11-19), not LangGraph node executions. A final answer on the last allowed call is a success. Asking for another call after that fails the trace with "step limit reached (N model calls)". Because AgentVersions are immutable, this meaning is a data contract.
   - `max_steps` is removed before parameters go to the SDK.
   - Allowed parameter keys are exactly `max_steps` and `max_tokens`. `POST /api/v1/runs` returns 422 for a version with any other key, so a typo never silently changes behaviour.
   - `temperature`, `top_p` and `top_k` were dropped (author decision, 2026-11-19) because Anthropic SDK 2.11.0 / API version 2026-10-21 no longer accepts them; a version that still has one gets a 422 naming the key, so nothing changes behaviour silently.
9. **Retries are ours and visible** (Claude's call, 2026-11-19).
   - The SDK's built-in retries are off (`max_retries=0`).
   - The adapter retries transient errors (429, 5xx, overloaded) up to 2 times with backoff, inside the same `llm` span. Each failed attempt is recorded as a span event with its error. The span records the attempt count (`agentops.llm.attempts`).
   - If every attempt fails, the span errors and the trace fails.
   - Span timing therefore includes the retries, and the trace shows why a call was slow.
10. **Ingest builds traces from span data alone** (follows from decision 2). The root span carries the run metadata as attributes: `agentops.agent_version_id`, `agentops.input`, `agentops.output` and `agentops.error`. Status comes from the root span's status. The runtime passes ingest nothing outside the spans, so a future OTLP receiver feeds the same interface.
11. **Only the author runs `make record`** (author decision, 2026-11-19). It costs money, so no agent runs it and no agent is given the key.
   - The adapter task builds the adapter, the `make record` script and the replay tests. Until real recordings exist, its tests use fixtures built from the SDK's own response types.
   - That task is a **checkpoint**. After the Reviewer's PASS, the checkpoint review guide asks the author to run `make record` on the PR branch and push the recorded fixtures. The Orchestrator re-runs `pr-gate` and dispatches a scoped re-review of the fixture diff. The author then applies `checkpoint-approved`, and the PR merges.
   - Fixtures are **saved responses replayed regardless of the request**, not recordings that must match the exact request. Prompt or tool-definition changes therefore never force a re-record. Only an SDK upgrade, a model ID change or a new response scenario does, and the fixture guard says which.

## 5. Approaches considered (how spans are produced)

| Option | Summary | Trade-off |
| ------ | ------- | --------- |
| **A. Own instrumentation on the plain Anthropic SDK (chosen)** | The LangGraph model node calls the Anthropic SDK through our provider adapter, which does the strict parsing. The tools node calls the registry. Each node opens its own OTel spans with `gen_ai.*` attributes. | Full control over span content and error reporting. Fewest dependencies for the drift guard to track. A clean tree for M4. Costs: more code of our own, and we write the OpenAI adapter ourselves later. |
| B. LangChain model wrappers plus an off-the-shelf instrumentor (OpenInference or OpenLLMetry) | Spans come almost for free, and the provider is a LangChain setting. | A noisy tree with one span per internal step. Attribute naming is set by a third party and has changed before. Parsing happens inside LangChain, which works against decision 3's loud failure. More packages to pin and re-record. |
| C. LangChain model wrappers plus our own callback handler | LangChain handles providers, and we choose which spans exist. | LangChain's callback lifecycle (run IDs, async ordering) is subtle. Parsing still happens inside LangChain. |

**Chosen: A** (author agreed). The graph is one small, generic loop that we own, so hand-written spans are little code. A also gives M4 the cleanest traces and the loudest failures. B would fit if we were tracing many LangChain agents that we didn't write, but that is the future OTLP ingest's job, not the runtime's.

## 6. Architecture

### 6.1 Modules

These follow the target layout in `README.md`. M1 creates only what it uses.

```
backend/app/
  agents/
    graph.py        # builds the generic LangGraph tool-calling graph from an AgentVersion
    runtime.py      # runs one input: opens the root span, enforces the step limit, hands spans to ingest
    tools.py        # tool registry (name → schema + async callable); get_weather
    providers/      # provider interface + Anthropic adapter (strict parsing, gen_ai.* attributes)
  observability/
    tracing.py      # OTel setup, span-collection hook, attribute names
    ingest.py       # OTel spans → Trace/Span rows (order-independent, idempotent)
  api/runs.py, api/traces.py
  services/runs.py, services/traces.py
  schemas/run.py, schemas/trace.py
```

### 6.2 A run, step by step

1. `POST /api/v1/runs` resolves the AgentVersion. Errors: 404 if the version doesn't exist, 422 if it names an unknown tool or an unsupported provider, 503 if `ANTHROPIC_API_KEY` is not set. No trace is created for these errors.
2. The runtime opens a root `chain` span for the run. Its OTel trace ID becomes `traces.otel_trace_id`.
3. The graph loops:
   - Each model call is an `llm` span. It records the request (system prompt, messages, tool definitions) as input and the parsed response content as output. Attributes include `gen_ai.system`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` and `gen_ai.response.finish_reasons`.
   - Each tool call is a `tool` span, with the arguments as input and the result as output.
4. The run ends when the model answers, when it hits the step limit, or on an error: a provider error, a parse failure or a tool exception. The failing span gets `status=error` and an `error` message. The trace gets `status=failed`, and its `error` names the cause.
5. All of the run's finished spans go to the ingest service. The service writes the Trace and Span rows in the request's transaction. The trace stores the user input, the final answer as output, the status and the start and end times.
6. The endpoint returns `201` with the trace's ID and status. **A failed run is data, not an HTTP error.** It still returns 201, and the trace says why it failed.

M1 must not rely on a background exporter thread to store spans. By the time step 6 returns, every span has to be stored. The builder chooses the mechanism, for example a span processor that collects spans per run and hands them to ingest synchronously.

### 6.3 Data model changes (migration `0002`)

- `spans`: `parent_span_id` (a UUID foreign key to `spans.id`) is replaced by `parent_otel_span_id` (text, nullable, no foreign key), resolved within the same trace (decision 1).
- `traces` and `spans` otherwise keep their M0 shape. Anything else the builder needs, such as a trace-level total token count, is added in this migration and noted in the PR.
- The migration round-trip test (upgrade, downgrade, upgrade) still passes.
- `docs/architecture.md` is updated: the ERD, the deletion rules and the API table.

### 6.4 API

| Method and path | Purpose |
| --------------- | ------- |
| `POST /api/v1/runs` | Body: `{agent_version_id, input}`. Runs synchronously and returns `201 {trace_id, status}`. Errors as in 6.2, step 1. |
| `GET /api/v1/traces` | Lists traces, newest first. Filters: `agent_version_id`, `status`. Standard pagination. |
| `GET /api/v1/traces/{id}` | The trace and its spans as a nested tree (children ordered by `started_at`), including input, output, attributes, status, error and timing. 404 if not found. |

The frontend's generated types are regenerated (`make api-types`). No UI is added.

### 6.5 Testing

- **Runtime and tracing:**
  - Tests use a scripted fake provider that returns a given sequence of responses (tool use, then an answer). They assert the stored rows: span kinds, the parent tree, attributes, statuses, and the step limit producing a failed trace.
  - Ingest is tested with spans delivered out of order and twice.
- **Anthropic adapter:** tests replay recorded real responses (decision 3). They cover a tool-use response, a final answer, and an unexpected or malformed response, which must produce an error span and a failed trace.
- **Fixture guard:** a gate test compares the SDK version and model IDs recorded in the fixtures with the locked SDK version and the model IDs in use.
- **API:** httpx against real Postgres, as in M0.
- `make record` and `make smoke` need the key and are not part of `make check` or `ci`.

### 6.6 Make targets

- `make record`: re-record the adapter fixtures against the real API.
- `make smoke`: one real run of the seeded `weather-bot` on a weather question, printing the trace tree rebuilt from the database. It needs `make db`, migrations and the seed data.

Both stop with a clear message if `ANTHROPIC_API_KEY` is missing.

## 7. Workflow notes for the plan

- **Checkpoints:**
  - The task that adds migration `0002` and the ingest service (Claude's call, following M0's pattern). It fixes how traces are stored, which every later milestone reads.
  - The Anthropic adapter task (decision 11), because the author records its fixtures before it merges.
  - Tasks that depend on either one wait for the author's `checkpoint-approved` label.
- The M1 chores [#22](https://github.com/edward-sf/AgentOps/issues/22), [#24](https://github.com/edward-sf/AgentOps/issues/24) and [#29](https://github.com/edward-sf/AgentOps/issues/29) become plan tasks, so their issues are reconciled to match plan task IDs (CONTRIBUTING, "Plans are just-in-time"). #24's "log the swallowed exception in /health" fits once observability setup lands. [#23](https://github.com/edward-sf/AgentOps/issues/23) is closed by the migration task.

## 8. Left to the builder

- Exact dependency versions: `langgraph`, `anthropic`, `opentelemetry-api` and `opentelemetry-sdk`, all latest stable at implementation time.
- The fixture file format and storage, within decision 11 (saved responses, not request-matched), as long as replay needs no key and the fixture guard can read the recorded versions.
- How spans reach ingest synchronously (6.2), and how OTel providers are set up so tests stay isolated.
- The exact span names and any extra `gen_ai.*` or `agentops.*` attributes beyond those listed in 6.2.
- The fixed weather data, and the `make smoke` question and output format.
- Indexes beyond the uniques and foreign keys stated here.
- How the plan splits this work into tasks, apart from the checkpoint in section 7.
