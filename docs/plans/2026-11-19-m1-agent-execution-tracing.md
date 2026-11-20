# M1 — Agent Execution and Tracing: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Follow `CONTRIBUTING.md` for branches, PRs, and records.

**Goal:** A LangGraph agent, built from an AgentVersion, runs on one input through `POST /api/v1/runs`. Every LLM call and tool call is recorded as an OpenTelemetry span and stored as Trace and Span rows. `GET /api/v1/traces/{id}` rebuilds the run from the database alone.

**Architecture:**
- `app/agents/` holds a provider interface with a strict Anthropic adapter on the plain SDK, a tool registry, and one generic tool-calling graph.
- The runtime opens OTel spans itself and collects them per run in memory.
- `app/observability/ingest.py` turns finished spans into rows. It doesn't care about order and is idempotent. It is the only way spans reach the database.
- Runs are synchronous. CI never calls a model API. Adapter tests replay saved responses through an `httpx.MockTransport`.

**Tech Stack:** M0's stack (Python 3.12, uv, FastAPI, SQLAlchemy 2.0 async, psycopg 3, Alembic, Pydantic v2, pytest), plus `langgraph`, `anthropic`, `opentelemetry-api` and `opentelemetry-sdk`.

**Spec:** [`docs/specs/2026-11-19-m1-agent-execution-tracing-design.md`](../specs/2026-11-19-m1-agent-execution-tracing-design.md). Section numbers like "D3" below refer to its section 4 decisions.

## Global Constraints

- All M0 constraints still hold:
  - routes under `/api/v1`
  - pagination `{items, total, limit, offset}` (limit default 50, max 200)
  - errors `{"detail": …}` with 404, 409 and 422
  - enums as `text` + `CHECK`
  - tests against real Postgres
  - generated frontend types committed (`make api-types`)
- **No test, `make check` step or `ci` job calls a model API or needs `ANTHROPIC_API_KEY`.** Only `make record` and `make smoke` call the API, and only the author runs `make record` (D3, D11).
- **No agent runs `make record`,** and no agent asks for the key.
- Spans link to parents by `parent_otel_span_id` (text, no FK), resolved within the same trace (D1).
- Spans reach the database only through `app.observability.ingest.ingest_spans` (D2, D10). The runtime passes it nothing except span records.
- The Anthropic SDK client is always built with `max_retries=0`. Retries belong to the adapter (D9).
- Run outcomes follow D7 exactly:
  - `tool_use` continues the loop.
  - `end_turn`, `stop_sequence` and `refusal` → `completed`.
  - `max_tokens` → `failed`, "answer truncated at max_tokens".
  - Anything else → `failed`, "unexpected stop reason: <reason>".
- `max_steps` counts model calls, default **10**. The allowed parameter keys are exactly `max_steps` and `max_tokens` (D8).
- An agent failure is data: `POST /api/v1/runs` returns **201** with `status: "failed"`. 404, 422 and 503 are only for configuration errors, and they create no trace (spec 6.2).
- LangChain model wrappers and third-party OTel instrumentors are not used (D6).

## Review Focus

1. **Spans arrive child-first, or the same batch arrives twice:** each span is stored once and the tree is correct. (Test in M1.T2.)
2. **The model calls a tool that isn't in the version's tool list:** the tool span errors and the trace fails with "model called unknown tool: <name>". It must not crash or loop. (Test in M1.T4.)
3. **The final answer comes on exactly the `max_steps`-th model call:** `completed`. Asking for one more call fails the trace with "step limit reached (N model calls)". (Test in M1.T4.)
4. **A span whose parent ID isn't in the trace (orphan):** `GET /api/v1/traces/{id}` returns it as a root. It is never dropped, and the endpoint never returns 500. (Test in M1.T3.)
5. **A transient 429 followed by success:** one `llm` span, `agentops.llm.attempts == 2`, and one `gen_ai.retry` event. Three 429s in a row: a failed trace whose error names the API error. (Tests in M1.T1 and M1.T4.)

---

## Milestone start (Orchestrator, once)

1. Reconcile the existing M1 issues so that `milestone-start` updates them instead of creating duplicates. It matches issues on the `M1.T<n>` title prefix. Run `gh issue edit <n> --title "<new title>"` for each:
   - #23 → `M1.T2 Trace storage: migration 0002 and ingest`
   - #22 → `M1.T6 flow hardening from the M0 review`
   - #24 → `M1.T7 Health logging, Makefile coverage, test factories`
   - #29 → `M1.T8 CI actions on Node 24`
2. Make sure the spec, this plan and the roadmap update exist in the working tree. Then run `./flow milestone-start 1 --dry-run`, check it shows 4 updates and 4 creates, and run `./flow milestone-start 1`.
3. Suggested order (one Implementer at a time): T1 → T2. While either checkpoint waits for the author, do T6, T8 and T3 (after T2 merges). Then T4 → T5 → T7.

---

## Tasks

### M1.T1 Provider interface and Anthropic adapter

**Depends on:** none
**Checkpoint**

**Goal:** A provider-neutral model interface, and an Anthropic implementation on the plain SDK. It parses responses strictly, retries transient errors visibly on the current span, and is tested by replaying saved real responses. It also adds `make record`, which only the author runs, and a CI guard that fails when the fixtures no longer match the locked SDK or the models in use.

**Acceptance criteria**
- [ ] `AnthropicProvider.complete` returns a `ModelResponse` for the tool-use, final-answer, direct-answer and `max_tokens` fixtures. It raises `ProviderResponseError` for an unknown content-block type, and passes an unknown stop reason through (D7).
- [ ] Transient errors (429, 500–599, `overloaded_error`) are retried up to 3 attempts in total. Each failed attempt adds a `gen_ai.retry` event to the current span, and the span gets `agentops.llm.attempts`. After the last failed attempt the adapter raises `ProviderAPIError`. Non-transient errors (400, 401, 403, 404) are not retried.
- [ ] Fixtures are saved response bodies replayed through `httpx.MockTransport`, whatever request is sent. No test needs a key or the network.
- [ ] `test_fixture_guard` fails, with a message to run `make record`, when a fixture's `sdk_version` differs from the installed `anthropic` version, or when an `anthropic` model in `datasets/sample.json` has no recorded fixture.
- [ ] `make record` refuses without `ANTHROPIC_API_KEY`. With the key, it rewrites the recordable fixtures with `"source": "recorded"`.
- [ ] Checkpoint: after the Reviewer's PASS, the author runs `make record` on the branch and pushes. The Orchestrator re-gates, a scoped re-review checks the fixture diff, and the author applies `checkpoint-approved`.

**Files:**
- Create: `backend/app/agents/__init__.py`, `backend/app/agents/providers/__init__.py`, `base.py`, `anthropic.py`, `record.py`
- Create: `backend/tests/agents/__init__.py`, `backend/tests/agents/fixtures/anthropic/*.json`, `backend/tests/agents/replay.py`, `backend/tests/agents/test_anthropic_provider.py`, `backend/tests/agents/test_fixture_guard.py`
- Modify: `backend/pyproject.toml` (+ `anthropic`, `opentelemetry-api`, `opentelemetry-sdk`), `backend/app/config.py`, `Makefile` (+ `record`), `.env.example` (comment that `ANTHROPIC_API_KEY` is used by `make record`/`make smoke` and the API)

**Interfaces:**
- Produces, in `app/agents/providers/base.py`:
  - `ToolSpec(name: str, description: str, input_schema: dict[str, Any])`, frozen dataclass.
  - `ToolCall(id: str, name: str, arguments: dict[str, Any])` and `ToolResult(tool_call_id: str, content: str, is_error: bool = False)`.
  - `UserTurn(text: str)`, `AssistantTurn(text: str | None, tool_calls: tuple[ToolCall, ...])` and `ToolResultsTurn(results: tuple[ToolResult, ...])`. `Turn = UserTurn | AssistantTurn | ToolResultsTurn`.
  - `StopReason = Literal["tool_use", "end_turn", "stop_sequence", "refusal", "max_tokens"]`
  - `ModelRequest(model: str, system: str, turns: list[Turn], tools: list[ToolSpec], params: dict[str, Any])`. `params` holds only SDK parameters; `max_steps` has already been removed.
  - `ModelResponse(text: str | None, tool_calls: tuple[ToolCall, ...], stop_reason: str, response_model: str, input_tokens: int, output_tokens: int)`.
    - `stop_reason` is a plain `str`, so the runtime (D7) decides what an unknown value means.
    - The adapter raises `ProviderResponseError` only for a missing or ill-typed field or an unknown content-block type.
  - `class Provider(Protocol)`: `name: str` and `async def complete(self, request: ModelRequest) -> ModelResponse`.
  - Errors: `ProviderError(Exception)`, `ProviderResponseError(ProviderError)` and `ProviderAPIError(ProviderError)`.
- Produces, in `app/agents/providers/anthropic.py`:
  - `AnthropicProvider(client: anthropic.AsyncAnthropic, *, max_attempts: int = 3, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep)`, with `name = "anthropic"`. Backoff is `0.5 * 2**(attempt-1)` seconds.
  - `make_anthropic_client(api_key: str, http_client: httpx.AsyncClient | None = None) -> anthropic.AsyncAnthropic`. It always passes `max_retries=0`.
- Produces: `Settings.anthropic_api_key: str | None = None`, read from env `ANTHROPIC_API_KEY`.
- Produces: the fixture file format. Each file in `tests/agents/fixtures/anthropic/<scenario>.json` is:
  ```json
  {"scenario": "tool_use", "source": "recorded", "sdk_version": "x.y.z", "model": "claude-sonnet-5-5",
   "recorded_at": "2026-11-19T00:00:00Z", "status_code": 200, "body": { "...raw Messages API response..." }}
  ```
  - `source` is `"recorded"` or `"constructed"`.
  - `RECORDABLE_SCENARIOS = ("tool_use", "final_answer", "direct_answer", "max_tokens")` lives in `app/agents/providers/record.py`. These must end up `recorded`.
  - The constructed-only scenarios are `refusal`, `unknown_block` and `unknown_stop_reason`, which can't be triggered reliably, plus `overloaded` (status 529 with an `overloaded_error` body).
  - Until the author records, the recordable fixtures are `"constructed"`, hand-written from the SDK's response types, with `sdk_version` set to the locked version.
- Produces: `tests/agents/replay.py::replay_client(*fixtures: str) -> anthropic.AsyncAnthropic`. It returns each fixture's `status_code` and `body` in order, whatever the request.

- [ ] **Step 1: Add dependencies.** Run `uv add anthropic opentelemetry-api opentelemetry-sdk`, and add `anthropic_api_key` to `Settings`.
- [ ] **Step 2: Write the failing adapter tests** in `test_anthropic_provider.py`, using `replay_client`. Each test runs inside a span from an in-memory `TracerProvider`, so events can be asserted.
  - `test_tool_use_response_parsed`: `stop_reason == "tool_use"`, one `ToolCall` with `name == "get_weather"`, `arguments["location"] == "Seattle"`, and `input_tokens > 0`.
  - `test_final_answer_parsed` and `test_direct_answer_parsed`: `stop_reason == "end_turn"`, `text` non-empty, `tool_calls == ()`.
  - `test_max_tokens_parsed`: `stop_reason == "max_tokens"`.
  - `test_refusal_parsed`: `stop_reason == "refusal"`.
  - `test_unknown_stop_reason_passed_through`: `stop_reason == "<value in fixture>"`.
  - `test_unknown_block_raises`: `pytest.raises(ProviderResponseError, match="unknown_block_type")`.
  - `test_request_mapping`: inspect the request the transport captured. `system` equals the request's system. The turns map to Anthropic `messages`, with tool results as `tool_result` blocks. The tools map to `{name, description, input_schema}`. `params` are passed through.
  - `test_transient_error_retried_then_succeeds`: fixtures `overloaded`, then `final_answer`. Assert one `gen_ai.retry` event with an `error.type` attribute, `agentops.llm.attempts == 2`, and `sleep` called once with 0.5.
  - `test_retries_exhausted_raises`: three `overloaded` → `ProviderAPIError`, `agentops.llm.attempts == 3`.
  - `test_auth_error_not_retried`: a 401 body → `ProviderAPIError` after one attempt.
  - `test_client_has_no_sdk_retries`: `make_anthropic_client("k").max_retries == 0`.
- [ ] **Step 3: Write the failing guard tests** in `test_fixture_guard.py`.
  - `test_fixture_guard`: for every fixture, `sdk_version == importlib.metadata.version("anthropic")`. Every `anthropic` model in `datasets/sample.json` appears as `model` in at least one fixture of each recordable scenario. The failure message contains `run 'make record'`.
  - `test_every_scenario_has_a_fixture`: one file per scenario.
- [ ] **Step 4: Run `uv run pytest tests/agents -q`.** Expected: failures and import errors.
- [ ] **Step 5: Implement `base.py` and `anthropic.py`.**
  - Map SDK `text` and `tool_use` blocks only. Any other block type raises `ProviderResponseError`.
  - Classify transient errors by the SDK exception: `RateLimitError`, `InternalServerError`, and `APIStatusError` with status ≥ 500.
  - Record retries with `opentelemetry.trace.get_current_span()`.
- [ ] **Step 6: Write the fixtures and `replay.py`. Run the tests.** Expected: all pass.
- [ ] **Step 7: Implement `record.py` and `make record`.**
  - `make record` runs `cd backend && uv run python -m app.agents.providers.record`.
  - The script uses the seeded `weather-bot` version 1 prompt, model and `get_weather` tool spec (inline, so it needs no database):
    - `tool_use`: "Find the weather in Seattle".
    - `final_answer`: the same, plus a tool result.
    - `direct_answer`: "What is the capital of France?"
    - `max_tokens`: the Seattle prompt with `max_tokens=5`.
  - It saves the raw response through `client.messages.with_raw_response.create`, and exits 2 with `ANTHROPIC_API_KEY is not set` when the key is missing.
  - Do not run it with a real key.
- [ ] **Step 8: Run `make check`.** Expected: all `✓`.
- [ ] **Step 9: Open the PR.** Run `./flow issue-pr <n> "feat(agents): add provider interface and Anthropic adapter"`. In `--summary`, say that the recordable fixtures are `constructed` until the author runs `make record`.
- [ ] **Step 10 (Orchestrator):** after PASS, post a checkpoint review guide. It asks the author to check out the branch, run `make record`, commit the fixtures and `git push`. It also asks them to review the D7 and D9 handling and the fixture format. After the push, re-run `pr-gate` and dispatch a re-review scoped to the fixture diff. It checks that only fixture files changed, every recordable fixture is `"source": "recorded"`, and `test_fixture_guard` passes.

---

### M1.T2 Trace storage: migration 0002 and ingest

**Depends on:** none
**Checkpoint**

**Goal:** Migration `0002` changes span parentage to OTel span IDs and adds span events. The ingest service turns finished OTel spans into Trace and Span rows, in any order and idempotently, with the span-attribute contract that the runtime and any future OTLP receiver both use. This resolves #23.

**Acceptance criteria**
- [ ] After migration `0002`, `spans` has `parent_otel_span_id text NULL` (no FK) and `events jsonb NOT NULL DEFAULT '[]'`, and no `parent_span_id`. Upgrade → downgrade base → upgrade still passes.
- [ ] `ingest_spans` stores a whole run delivered in any order, and storing it a second time changes nothing.
- [ ] Trace fields come only from the root span: `agent_version_id`, `input`, `output`, `error`, `status`, `started_at`, `ended_at`.
- [ ] A batch whose spans belong to a trace that isn't stored yet, without that trace's root span, raises `IngestError` and stores nothing.
- [ ] `docs/architecture.md`'s ERD and deletion table show the new span columns. The `Span → parent Span` cascade row is removed.
- [ ] Checkpoint: the author approves (`checkpoint-approved`) before M1.T3 and M1.T4 start.

**Files:**
- Create: `backend/alembic/versions/0002_span_otel_parent.py`, `backend/app/observability/__init__.py`, `attributes.py`, `ingest.py`
- Create: `backend/tests/factories.py`, `backend/tests/observability/__init__.py`, `test_ingest.py`
- Modify: `backend/app/models/trace.py`, `backend/tests/models/test_constraints.py` (the span tests use the new column), `docs/architecture.md`

**Interfaces:**
- Produces, in `app/observability/attributes.py` (string constants):
  - `SPAN_KIND = "agentops.span.kind"`, with values `llm|tool|chain`.
  - `AGENT_VERSION_ID = "agentops.agent_version_id"`
  - `INPUT = "agentops.input"` and `OUTPUT = "agentops.output"`: JSON-encoded strings.
  - `ERROR = "agentops.error"`
  - `LLM_ATTEMPTS = "agentops.llm.attempts"`
  - The `gen_ai.*` names used in M1.T4: `GEN_AI_OPERATION = "gen_ai.operation.name"`, `GEN_AI_SYSTEM = "gen_ai.system"`, `GEN_AI_REQUEST_MODEL = "gen_ai.request.model"`, `GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"`, `GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"`, `GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"`, `GEN_AI_FINISH_REASONS = "gen_ai.response.finish_reasons"`, `GEN_AI_TOOL_NAME = "gen_ai.tool.name"`, `GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"`.
- Produces, in `app/observability/ingest.py`:
  - `SpanRecord(otel_trace_id: str, otel_span_id: str, parent_otel_span_id: str | None, name: str, started_at: datetime, ended_at: datetime, status: Literal["unset","ok","error"], status_message: str | None, attributes: dict[str, Any], events: list[dict[str, Any]])`, a frozen dataclass.
    - IDs are lowercase hex: 32 characters for traces, 16 for spans.
    - Each event is `{"name", "timestamp" (ISO), "attributes"}`.
  - `from_readable_span(span: opentelemetry.sdk.trace.ReadableSpan) -> SpanRecord`
  - `async def ingest_spans(session: AsyncSession, records: Sequence[SpanRecord]) -> list[uuid.UUID]` returns the IDs of the trace rows the batch touched. It flushes but does not commit.
  - `class IngestError(Exception)`
- Mapping rules:
  - The span's `kind` comes from `agentops.span.kind`. If that's missing, `gen_ai.operation.name` `chat`→`llm` and `execute_tool`→`tool`. Otherwise `other`.
  - `agentops.input` and `agentops.output` are JSON-decoded into `Span.input` and `Span.output` and removed from the stored `attributes`.
  - `Span.error` is the `status_message` when the status is `error`.
  - The root span (`parent_otel_span_id is None`) creates the Trace:
    - `agent_version_id` comes from `agentops.agent_version_id`. If it's missing or not a UUID, raise `IngestError`.
    - `input` and `output` are the decoded root input and output, when they are strings.
    - `error` is `agentops.error`.
    - `status` is `failed` if the root status is `error`, otherwise `completed`.
  - Inserts use `ON CONFLICT DO NOTHING`: on `otel_trace_id` for traces, and on `(trace_id, otel_span_id)` for spans.
- Produces, in `tests/factories.py`:
  - `async def make_agent_version(session, *, tools: list[str] = ["get_weather"], parameters: dict[str, Any] | None = None, provider: str = "anthropic") -> AgentVersion`. It creates its own Agent with a unique name.
  - `def span_record(**overrides) -> SpanRecord`, with valid defaults.

- [ ] **Step 1: Write the failing migration assertions.** Extend `test_migrations.py` to assert the `spans` columns after `upgrade head`: `parent_otel_span_id` present, `parent_span_id` absent, `events` present. Run it. Expected: FAIL.
- [ ] **Step 2: Write `0002` and update the `Span` model.** Drop the `parent_span_id` FK, its index and the column. Add the two new columns. `downgrade` restores the M0 shape, and parent links are lost on downgrade. Run the migration tests. Expected: PASS.
- [ ] **Step 3: Write the failing ingest tests** in `test_ingest.py`, using `db_session`, `make_agent_version` and `span_record`. The basic run is a root `chain`, an `llm` child and a `tool` child of the root.
  - `test_ingest_builds_trace_and_spans`: one trace, `status == "completed"`, `input` and `output` from the root, 3 spans with the right `kind`s, and both children with `parent_otel_span_id == root.otel_span_id`.
  - `test_ingest_children_before_root`: records passed as `[tool, llm, root]` give the same result.
  - `test_ingest_is_idempotent`: ingesting the same batch twice leaves 1 trace and 3 spans.
  - `test_ingest_failed_root`: a root with `status="error"` and `agentops.error="step limit reached (10 model calls)"` gives `status == "failed"` and that `error`.
  - `test_ingest_strips_io_attributes`: `Span.input == {"location": "Seattle"}`, and `"agentops.input" not in Span.attributes`.
  - `test_ingest_stores_events`: one event in, one event in `Span.events`.
  - `test_ingest_unknown_trace_without_root_raises`: `pytest.raises(IngestError)`, and the span count stays 0.
  - `test_ingest_root_without_agent_version_raises`.
  - `test_from_readable_span`: a span made by an SDK `TracerProvider` with an `InMemorySpanExporter` maps to hex IDs, the parent ID, the status and the attributes.
- [ ] **Step 4: Run `uv run pytest tests/observability tests/models -q`.** Expected: failures.
- [ ] **Step 5: Implement `attributes.py` and `ingest.py`.** Use `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_nothing(...)`. If the trace row already exists, look it up by `otel_trace_id`.
- [ ] **Step 6: Run the tests.** Expected: PASS. Update `docs/architecture.md` (the ERD `SPAN` block, the self-relation and the deletion table).
- [ ] **Step 7: Run `make check`, then `./flow issue-pr <n> "feat(observability): store spans by otel parent id and add span ingest"`.** The Orchestrator posts the checkpoint guide. The decisions to approve are the attribute contract and the `IngestError` rule for root-less batches.

---

### M1.T3 Traces read API

**Depends on:** M1.T2

**Goal:** `GET /api/v1/traces` and `GET /api/v1/traces/{id}`. The detail endpoint rebuilds the span tree from stored rows.

**Acceptance criteria**
- [ ] `GET /api/v1/traces` lists traces newest first, with filters `agent_version_id` and `status` (an invalid status gives 422) and standard pagination.
- [ ] `GET /api/v1/traces/{id}` returns the trace and `spans`, a list of root `SpanNode`s with nested `children` ordered by `started_at`. 404 if the trace doesn't exist.
- [ ] An orphan span (its parent ID isn't in the trace) is returned as a root.
- [ ] `make api-types` is re-run, and `docs/architecture.md`'s API table lists both endpoints.

**Files:**
- Create: `backend/app/schemas/trace.py`, `backend/app/services/traces.py`, `backend/app/api/traces.py`, `backend/tests/api/test_traces.py`
- Modify: `backend/app/main.py`, `frontend/lib/api/openapi.json`, `frontend/lib/api/schema.ts`, `docs/architecture.md`

**Interfaces:**
- Consumes: `ingest_spans`, `span_record` and `make_agent_version` (M1.T2). Tests build traces through ingest only.
- Produces:
  - `TraceRead(id, otel_trace_id, agent_version_id, evaluation_run_id, test_case_id, status, input, output, error, started_at, ended_at)`
  - `SpanNode(id, otel_span_id, parent_otel_span_id, name, kind, status, attributes, events, input, output, error, started_at, ended_at, children: list[SpanNode])`
  - `TraceDetail(TraceRead)`, with `spans: list[SpanNode]`.
  - `services.traces.list_traces(session, *, agent_version_id: UUID | None, status: str | None, limit: int, offset: int) -> tuple[list[Trace], int]`
  - `services.traces.get_trace_detail(session, trace_id: UUID) -> TraceDetail | None`

- [ ] **Step 1: Write the failing tests** in `test_traces.py`:
  - `test_get_trace_returns_tree`: ingest a root plus `llm` plus `tool`. The response has `spans[0].kind == "chain"`, its `children` kinds are `["llm", "tool"]` (in `started_at` order), and `input` and `output` match.
  - `test_get_trace_orphan_span_is_root`: a span whose `parent_otel_span_id="ffffffffffffffff"` appears in the top-level `spans`.
  - `test_get_trace_404`.
  - `test_list_traces_filters_and_orders`: two versions and three traces. Filtering by `agent_version_id` returns only that version's traces, newest `started_at` first. `status=failed` filters, and `status=bogus` gives 422.
  - `test_list_traces_pagination`: `{items, total, limit, offset}`, and `limit=201` gives 422.
- [ ] **Step 2: Run `uv run pytest tests/api/test_traces.py -q`.** Expected: FAIL (404s).
- [ ] **Step 3: Implement the schemas, the services and the router.** Build the tree in Python from one `SELECT` of the trace's spans, keyed by `otel_span_id`.
- [ ] **Step 4: Run the tests.** Expected: PASS. Run `make api-types` and update `docs/architecture.md`.
- [ ] **Step 5: Run `make check`, then `./flow issue-pr <n> "feat(api): add traces list and detail endpoints"`.**

---

### M1.T4 Agent runtime: tools, graph and tracing

**Depends on:** M1.T1, M1.T2

**Goal:** Build the generic LangGraph tool-calling agent from an AgentVersion. Run it on one input with OTel spans for the run, each model call and each tool call. Apply the D7 and D8 outcome rules, and store the run through `ingest_spans`. All tests use a scripted fake provider.

**Acceptance criteria**
- [ ] A scripted run (a tool call, then an answer) stores a `completed` trace. It has a `chain` root, two `llm` spans and one `tool` span. All three are children of the root. The trace has `output` set, and the `gen_ai.*` attributes are listed below.
- [ ] Every outcome in D7 and D8, the step limit, an unknown tool, a tool exception and a provider error produces the trace status and error text pinned below.
- [ ] `validate_parameters` rejects unknown keys, and a non-positive-integer `max_steps`, with `InvalidParametersError`. `max_steps` never reaches the provider's `params`.
- [ ] `get_weather` is deterministic, makes no network call, and returns the pinned values for Seattle and London.
- [ ] The run's spans come from a per-run `TracerProvider`, so nothing touches the global provider and parallel tests don't share spans.

**Files:**
- Create: `backend/app/agents/tools.py`, `backend/app/agents/config.py`, `backend/app/agents/graph.py`, `backend/app/agents/runtime.py`, `backend/app/observability/tracing.py`
- Create: `backend/tests/agents/fakes.py`, `backend/tests/agents/test_tools.py`, `backend/tests/agents/test_config.py`, `backend/tests/agents/test_runtime.py`
- Modify: `backend/pyproject.toml` (+ `langgraph`)

**Interfaces:**
- Consumes: `Provider`, `ModelRequest`, `ModelResponse`, the turn types, `ToolSpec`, `ToolCall`, `ToolResult`, `ProviderError` (M1.T1). `ingest_spans`, `from_readable_span`, the `attributes` constants and `make_agent_version` (M1.T2).
- Produces:
  - `app/agents/tools.py`:
    - `Tool(spec: ToolSpec, func: Callable[[dict[str, Any]], Awaitable[str]])`
    - `TOOLS: Mapping[str, Tool]`
    - `get_tools(names: Sequence[str]) -> list[Tool]`, which raises `UnknownToolError(name)`.
    - `get_weather` has input schema `{"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}`. It returns a JSON string `{"location", "temperature_c", "conditions", "raining"}`:
      - Seattle → `12, "light rain", true`
      - London → `9, "overcast", false`
      - any other location → `18, "clear", false`
      - Matching is case-insensitive.
      - A missing `location` raises `ValueError`.
  - `app/agents/config.py`:
    - `ALLOWED_PARAMETERS = {"max_steps", "max_tokens"}` and `DEFAULT_MAX_STEPS = 10`.
    - `RunParams(max_steps: int, model_params: dict[str, Any])`
    - `validate_parameters(parameters: dict[str, Any]) -> RunParams`, which raises `InvalidParametersError(message)`. If `max_tokens` is missing, `model_params` defaults it to 1024, because the Anthropic API requires it.
    - `AgentConfig(agent_version_id: UUID, provider: str, model: str, system_prompt: str, params: RunParams, tools: list[Tool])`
    - `AgentConfig.from_version(version: AgentVersion) -> AgentConfig`, which raises `InvalidParametersError` or `UnknownToolError`.
  - `app/observability/tracing.py`:
    - `class RunTracer`, a context manager owning a fresh `TracerProvider` with a `SimpleSpanProcessor` into an `InMemorySpanExporter`.
    - `.tracer: Tracer` and `.records() -> list[SpanRecord]`.
  - `app/agents/runtime.py`:
    - `RunOutcome(trace_id: UUID, status: Literal["completed", "failed"])`
    - `async def run_agent(*, session: AsyncSession, provider: Provider, config: AgentConfig, input: str) -> RunOutcome`. It calls `ingest_spans` and flushes; it does not commit.
  - `tests/agents/fakes.py`: `ScriptedProvider(script: list[ModelResponse | Exception])`, with `name = "anthropic"`. Its `.requests: list[ModelRequest]` holds the requests it received. When the script runs out, it raises `AssertionError("script exhausted")`.
- Span contract (the attribute names come from M1.T2):
  - **Root:** named `agent.run`.
    - `agentops.span.kind=chain`
    - `agentops.agent_version_id`
    - `agentops.input` (JSON string of the input)
    - `agentops.output` (JSON string of the final text) when completed
    - `agentops.error` and status ERROR when failed
  - **LLM span:** named `chat <model>`, a child of the root.
    - `agentops.span.kind=llm`, `gen_ai.operation.name=chat`, `gen_ai.system=anthropic`
    - `gen_ai.request.model`, `gen_ai.response.model`
    - `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens`
    - `gen_ai.response.finish_reasons=[stop_reason]`
    - `agentops.input` = JSON `{system, turns, tools, params}`
    - `agentops.output` = JSON `{text, tool_calls}`
    - The provider call runs with this span as the current span, so the adapter's retry events land on it.
  - **Tool span:** named `execute_tool <name>`, a child of the root.
    - `agentops.span.kind=tool`, `gen_ai.operation.name=execute_tool`
    - `gen_ai.tool.name`, `gen_ai.tool.call.id`
    - `agentops.input` = the arguments, `agentops.output` = the result string
- Outcome and error text (exact):

| Condition | Trace | `agentops.error` | Failing span |
| --- | --- | --- | --- |
| `end_turn`, `stop_sequence`, `refusal` | completed | — | — |
| `max_tokens` | failed | `answer truncated at max_tokens` | llm (status ERROR) |
| `tool_use` with no tool calls | failed | `model stopped for tool_use without calling a tool` | llm |
| any other stop reason | failed | `unexpected stop reason: <reason>` | llm |
| another call needed after `max_steps` calls | failed | `step limit reached (<max_steps> model calls)` | root only |
| model calls a tool not in `config.tools` | failed | `model called unknown tool: <name>` | tool |
| tool raises | failed | `tool <name> failed: <exception message>` | tool |
| `ProviderError` | failed | `provider error: <exception message>` | llm |

- [ ] **Step 1: Write the failing tool and config tests.**
  - `test_tools.py`:
    - `get_weather({"location": "seattle"})` decodes to `{"location": "seattle", "temperature_c": 12, "conditions": "light rain", "raining": true}`
    - London values
    - the default values
    - missing `location` raises `ValueError`
    - `get_tools(["nope"])` raises `UnknownToolError`
  - `test_config.py`:
    - `validate_parameters({})` gives `max_steps == 10` and `model_params == {"max_tokens": 1024}`
    - `{"max_steps": 3, "max_tokens": 512}` gives `model_params == {"max_tokens": 512}`
    - `{"temperature": 0}` raises `InvalidParametersError` matching `temperature`
    - `{"temprature": 0}` raises `InvalidParametersError` matching `temprature`
    - `{"max_steps": 0}` and `{"max_steps": "5"}` raise
- [ ] **Step 2: Write the failing runtime tests** in `test_runtime.py`, using `db_session`, `make_agent_version` and `ScriptedProvider`. Each test reads the stored rows back with `select`.
  - `test_tool_then_answer_completes`: script `[tool_use(get_weather, Seattle), end_turn("It is 12°C and raining")]`. Assert:
    - the trace is `completed`, with `output == "It is 12°C and raining"`
    - the span kinds are `{chain: 1, llm: 2, tool: 1}`, and every non-root span's parent is the root
    - the second request's last turn is a `ToolResultsTurn` holding the weather JSON
    - the llm span attributes include `gen_ai.usage.input_tokens`
  - `test_direct_answer_completes`: script `[end_turn]`, with no tool span.
  - `test_refusal_completes` and `test_stop_sequence_completes`.
  - `test_max_tokens_fails`, `test_unknown_stop_reason_fails`, `test_unknown_tool_fails`, `test_tool_exception_fails` (`get_weather` called with `{}`) and `test_provider_error_fails`: each asserts the status and error text from the table, and that the failing span has status `error`.
  - `test_answer_on_last_allowed_step_completes`: `max_steps=2`, script `[tool_use, end_turn]`. Completed.
  - `test_step_limit_fails`: `max_steps=2`, script `[tool_use, tool_use]`. Failed, `step limit reached (2 model calls)`, and exactly 2 llm spans.
  - `test_max_steps_not_sent_to_provider`: `"max_steps" not in provider.requests[0].params`.
  - `test_retry_event_lands_on_llm_span`: a provider stub that adds a `gen_ai.retry` event to the current span and then returns `end_turn`. The stored llm span's `events[0]["name"] == "gen_ai.retry"`.
- [ ] **Step 3: Run `uv run pytest tests/agents -q`.** Expected: the new tests fail.
- [ ] **Step 4: Implement `tools.py`, `config.py` and `tracing.py`.**
- [ ] **Step 5: Implement `graph.py`.**
  - `build_graph(provider: Provider, config: AgentConfig, tracer: Tracer) -> CompiledStateGraph`.
  - The nodes are `model` and `tools`. A conditional edge goes from `model` to `tools` on `tool_use`, and otherwise to END.
  - State: `turns`, `model_calls: int`, `final_text: str | None`, `failure: str | None`.
  - The `model` node fails the run when `model_calls == max_steps` before it calls the model.
  - Set the LangGraph `recursion_limit` to `2 * max_steps + 5`, so our own limit always triggers first.
- [ ] **Step 6: Implement `runtime.py`.** Open the root span, invoke the graph, set the root's attributes and status from the final state, end the root, then `ingest_spans(session, run_tracer.records())`. Run the tests. Expected: PASS.
- [ ] **Step 7: Run `make check`, then `./flow issue-pr <n> "feat(agents): add generic tool-calling runtime with tracing"`.**

---

### M1.T5 Runs API and `make smoke`

**Depends on:** M1.T3, M1.T4

**Goal:** `POST /api/v1/runs` runs an AgentVersion synchronously and returns its trace. `make smoke` proves the milestone against the real API.

**Acceptance criteria**
- [ ] `POST /api/v1/runs` with `{agent_version_id, input}` returns `201 {trace_id, status}`. A completed run and a failed run both return 201.
- [ ] Unknown version → 404. Unknown tool, provider other than `anthropic`, or invalid parameters → 422 with the reason in `detail`. Missing `ANTHROPIC_API_KEY` → 503 with `detail == "ANTHROPIC_API_KEY is not set"`. None of these creates a trace row.
- [ ] No database transaction is open while the model is called. The version is read and the read transaction ends first.
- [ ] `make smoke` runs the seeded `weather-bot` v1 on "Find the weather in Seattle" through `POST /api/v1/runs` and `GET /api/v1/traces/{id}`, in process, and prints the span tree. It exits non-zero if the trace isn't `completed` or has no `tool` span. It refuses without the key.
- [ ] `test_recordable_fixtures_are_recorded`: every `RECORDABLE_SCENARIOS` fixture has `"source": "recorded"`.
- [ ] `make api-types` is re-run. `docs/architecture.md` gains `POST /api/v1/runs` and a short "Agent runs" section (the flow in spec 6.2). README lists `make record` (author only) and `make smoke`.

**Files:**
- Create: `backend/app/schemas/run.py`, `backend/app/services/runs.py`, `backend/app/api/runs.py`, `backend/app/smoke.py`, `backend/tests/api/test_runs.py`
- Modify: `backend/app/agents/providers/__init__.py`, `backend/app/main.py`, `backend/tests/agents/test_fixture_guard.py`, `Makefile` (+ `smoke`), `frontend/lib/api/*`, `docs/architecture.md`, `README.md`

**Interfaces:**
- Consumes: `run_agent`, `AgentConfig.from_version`, `InvalidParametersError`, `UnknownToolError` and `ScriptedProvider` (M1.T4). `AnthropicProvider`, `make_anthropic_client` and `Settings.anthropic_api_key` (M1.T1). The traces endpoints (M1.T3).
- Produces:
  - In `app/agents/providers/__init__.py`:
    - `provider_for(name: str, settings: Settings) -> Provider`
    - It raises `UnsupportedProviderError(name)`, or `ProviderNotConfiguredError("ANTHROPIC_API_KEY is not set")` when the key is missing.
  - `app/api/deps.py` gains `ProviderFactoryDep`, a dependency returning `Callable[[str], Provider]`. Tests override it to return a `ScriptedProvider`.
  - `RunCreate(agent_version_id: UUID, input: str = Field(min_length=1))` and `RunRead(trace_id: UUID, status: Literal["completed", "failed"])`.
  - `services.runs.start_run(session, provider_factory, payload: RunCreate) -> RunOutcome`

- [ ] **Step 1: Write the failing tests** in `test_runs.py`, with the provider factory overridden to `ScriptedProvider`.
  - `test_run_completes`: a 201, with `status == "completed"`. Then `GET /api/v1/traces/{trace_id}` gives `status == "completed"` and a `tool` span among the root's children.
  - `test_failed_run_is_201`: the script ends with `max_tokens`. 201, `status == "failed"`.
  - `test_unknown_version_404`, `test_unknown_tool_422` (a version with `tools=["nope"]`), `test_openai_provider_422`, `test_invalid_parameters_422` (`{"temprature": 0}`) and `test_missing_key_503` (the real `provider_for` with `anthropic_api_key=None`). Each asserts the status, the `detail` text, and that the trace count is still 0.
  - `test_empty_input_422`.
- [ ] **Step 2: Add `test_recordable_fixtures_are_recorded`** to `test_fixture_guard.py`.
- [ ] **Step 3: Run the tests.** Expected: the API tests fail. The recorded-fixture test passes only if the author recorded in M1.T1. If it fails, stop and tell the Orchestrator.
- [ ] **Step 4: Implement `provider_for`, the dependency, `start_run` and the router.** `start_run` loads the version, builds `AgentConfig` (mapping errors to 404 or 422), calls `provider_factory` (`ProviderNotConfiguredError` → 503), commits to end the read transaction, then calls `run_agent`. Map the exceptions to HTTP errors in the router or `app/errors.py`, following M0's pattern.
- [ ] **Step 5: Run the tests.** Expected: PASS.
- [ ] **Step 6: Implement `app/smoke.py` and `make smoke`.** The target is `cd backend && uv run python -m app.smoke`.
  - Use `create_app()` with `LifespanManager` and an `httpx.AsyncClient(transport=ASGITransport(app))`, against `DATABASE_URL`.
  - Find `weather-bot` and its version 1 through the agents API, then post the run.
  - Print one line per span: indentation, kind, name, status, duration in ms, and the token counts for `llm` spans.
  - Exit 2 if the key is missing, and 1 if the run isn't completed or has no tool span.
  - Do not run it with a real key.
- [ ] **Step 7: Run `make api-types`, and update `docs/architecture.md` and `README.md`. Run `make check`, then `./flow issue-pr <n> "feat(api): add synchronous runs endpoint and make smoke"`.**

---

### M1.T6 flow hardening from the M0 review

**Depends on:** none

**Goal:** Close the `flow` gaps that the M0 final review deferred (#22).

**Acceptance criteria**
- [ ] `pr-gate` and `issue-merge` count `ci` as green only when every expected job (`backend`, `frontend`, `flow`, `contracts`, `docker`) is present and successful on head. A missing job is reported by name.
- [ ] `milestone-start`, `issue-start` and `review-context` read the plan from `origin/<base>` (`git show`), not the working tree. `milestone-start` reads it from the working tree only when it creates the branch.
- [ ] `pr-gate` retries for up to 60 s when there are no checks yet, instead of failing at once.
- [ ] `issue-merge` doesn't print `-> Merged to milestone` when the board update warned.
- [ ] The `milestone_pr` base filter is asserted in a test, and `milestone.anchor` is replaced by `cli.heading_anchor`.
- [ ] `milestone-start`'s start commit also includes the milestone's spec (`docs/specs/*-m<N>-*.md`). This commits the local patch the Orchestrator used to start M1; see the diff in `scripts/flow/flow/cli.py`.
- [ ] `milestone-start` ignores milestone issues whose title has no `M<N>.T<n>` prefix, without warning. Today it prints `warning: None matches issues #a and #b` for every one of them.

**Files:**
- Modify: `scripts/flow/flow/{config,guard,cli,milestone,plan}.py` (`cli.py` already has the uncommitted spec-inclusion patch in the working tree: keep it and commit it with its test), `scripts/flow/tests/{test_guard,test_cli,test_milestone,test_plan}.py`

**Interfaces:**
- Produces: `flow.config.REQUIRED_CI_JOBS = ("backend", "frontend", "flow", "contracts", "docker")`. `guard.ci_status(checks) -> tuple[bool, list[str]]` returns whether CI is green and the missing or failing job names.

- [ ] **Step 1: Write the failing tests:**
  - `test_guard.py::test_missing_required_job_blocks` (checks without `docker` → not green, `["docker"]`)
  - `test_cli.py::test_pr_gate_retries_when_no_checks` (a fake gh returns no checks twice, then green)
  - `test_cli.py::test_issue_merge_no_success_line_on_board_warning`
  - `test_milestone.py::test_milestone_pr_filters_base`
  - `test_plan.py::test_plan_read_from_origin_base` (the fake git `show` returns the plan text)
  - `test_cli.py::test_milestone_start_includes_spec` (a tmp repo with `docs/specs/x-m3-y-design.md` → the start files passed to `milestone.milestone_start` include it)
  - `test_milestone.py::test_untitled_issues_ignored_silently` (two issues without a task ID produce no warning and no update)
- [ ] **Step 2: Run `cd scripts/flow && uv run pytest -q`.** Expected: FAIL.
- [ ] **Step 3: Implement the changes.** Run the tests. Expected: PASS.
- [ ] **Step 4: Run `make check`, then `./flow issue-pr <n> "fix(flow): require named ci jobs, read plan from base, retry pr-gate"`.**

---

### M1.T7 Health logging, Makefile coverage, test factories

**Depends on:** M1.T2

**Goal:** Close the remaining M0 deferrals (#24).

**Acceptance criteria**
- [ ] `/health` logs the swallowed exception at WARNING, with `exc_info`, through `logging.getLogger("app.health")`. A test asserts it with `caplog`.
- [ ] `make test`, `make lint` and `make typecheck` also cover `scripts/flow`. `make fmt` also formats `scripts/flow` and runs `npm run lint -- --fix` in `frontend`.
- [ ] The dataset and agent API tests that build agent versions or traces use `tests/factories.py` (from M1.T2), with no duplicated setup left.

**Files:**
- Modify: `backend/app/api/health.py`, `backend/tests/api/test_health.py`, `Makefile`, `backend/tests/api/test_datasets.py`, `backend/tests/api/test_agents.py`

- [ ] **Step 1: Write the failing test** `test_health_logs_database_error`: `caplog` records one WARNING from `app.health` when the database is unreachable. Run it. Expected: FAIL.
- [ ] **Step 2: Add the log call.** Run the test. Expected: PASS.
- [ ] **Step 3: Update the Makefile targets.** Then run `make lint typecheck test`. Expected: the flow steps run, and everything passes.
- [ ] **Step 4: Refactor the tests onto the factories.** Run `uv run pytest -q`. Expected: the same pass count as before.
- [ ] **Step 5: Run `make check`, then `./flow issue-pr <n> "chore: log health errors, cover flow in make targets, share test factories"`.**

---

### M1.T8 CI actions on Node 24

**Depends on:** none

**Goal:** Upgrade the pinned GitHub Actions to the majors that run on Node 24, and silence the docker job's output warning (#29).

**Acceptance criteria**
- [ ] `.github/workflows/ci.yml` pins these versions:
  - `actions/checkout@v7`
  - `actions/setup-node@v7`
  - `astral-sh/setup-uv@v10`
  - `docker/setup-buildx-action@v4`
  - `docker/build-push-action@v7`

  Input changes from each release note are applied.
- [ ] Both `build-push-action` steps set `outputs: type=cacheonly`.
- [ ] On the PR's `ci` run, no job carries the Node 20 annotation, and the logs contain no `DEP0040`, `DEP0169` or `No output specified`. All five jobs are green.

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Read each action's release notes** between the old and new majors. List the input changes in the PR notes.
- [ ] **Step 2: Update the pins and inputs.** Add `outputs: type=cacheonly`.
- [ ] **Step 3: Run `./flow issue-pr <n> "ci: move actions to Node 24 majors"` and `./flow pr-gate <pr>`.** Then check the logs: `gh run view <id> --log | grep -E "Node.js 20|DEP0040|DEP0169|No output specified"`. Expected: no output.

---

## Milestone finish (Orchestrator)

1. On `milestone/m1`, run `make setup db migrate seed`, then `make check`.
2. Run `make smoke` once (spec D3: the Orchestrator runs it at verification; it is one agent run of 2 model calls). Paste the printed trace tree into the milestone PR as the exit-criteria evidence.
3. Run `./flow milestone-pr 1`, and fill in the Summary, Verification, Deviations and Review guide. Tick the exit criterion: "An agent completes a task; its execution can be reconstructed from stored trace and span records".
4. Create backlog issues for the deferred minor findings. Mark the PR ready and notify the author.
