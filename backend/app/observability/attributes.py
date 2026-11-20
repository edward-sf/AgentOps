"""The span-attribute contract shared by the runtime and ingest.

The runtime sets these on the OTel spans it opens; `ingest_spans` reads them back.
A future OTLP receiver feeds ingest the same attributes (M1 spec, decision 10).
"""

# agentops.* — our own run metadata.
SPAN_KIND = "agentops.span.kind"
"""`llm`, `tool` or `chain`. Without it, ingest falls back to `gen_ai.operation.name`."""
SPAN_KINDS = ("llm", "tool", "chain")

AGENT_VERSION_ID = "agentops.agent_version_id"
"""Root span only: the AgentVersion UUID the run used. Required on every root span."""
EVALUATION_RUN_ID = "agentops.evaluation_run_id"
"""Optional, root span only: the EvaluationRun UUID this run belongs to. Becomes
`Trace.evaluation_run_id`."""
TEST_CASE_ID = "agentops.test_case_id"
"""Optional, root span only: the TestCase UUID this run executed. Becomes
`Trace.test_case_id`."""
INPUT = "agentops.input"
"""JSON-encoded string. Stored in `Span.input`, not in `Span.attributes`."""
OUTPUT = "agentops.output"
"""JSON-encoded string. Stored in `Span.output`, not in `Span.attributes`."""
ERROR = "agentops.error"
"""Root span only: why the run failed. Becomes `Trace.error`."""
LLM_ATTEMPTS = "agentops.llm.attempts"
"""`llm` spans: how many API attempts the call took, retries included."""
LLM_THINKING_BLOCKS = "agentops.llm.thinking_blocks"
"""`llm` spans: how many `thinking` and `redacted_thinking` blocks the response had. Their
content is not stored (M2.T11)."""

# gen_ai.* — OpenTelemetry GenAI semantic conventions.
GEN_AI_OPERATION = "gen_ai.operation.name"
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"

GEN_AI_OPERATION_KINDS = {"chat": "llm", "execute_tool": "tool"}
"""Fallback kind for spans without `agentops.span.kind`."""
