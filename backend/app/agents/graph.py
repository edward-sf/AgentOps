"""The one generic tool-calling graph (spec D5, D7, D8).

A `model` node and a `tools` node loop until the model answers or the run fails. Each
model call is an `llm` span and each tool call a `tool` span, opened here on the run's
tracer as children of the current (root) span. A failure is never raised: the node
records it on its span and in `state["failure"]`, and the graph ends.
"""

import json
from dataclasses import asdict
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from app.agents.config import AgentConfig
from app.agents.providers.base import (
    AssistantTurn,
    ModelRequest,
    ModelResponse,
    Provider,
    ProviderError,
    ProviderFailureKind,
    ToolResult,
    ToolResultsTurn,
    Turn,
    UserTurn,
)
from app.observability import attributes as attr

COMPLETED_STOP_REASONS = frozenset({"end_turn", "stop_sequence", "refusal"})
"""D7: an answer, even a refusal. M3 judges its quality."""


class AgentState(TypedDict):
    turns: list[Turn]
    model_calls: int
    final_text: str | None
    failure: str | None
    provider_failure: ProviderFailureKind | None
    """Set with `failure` when a `ProviderAPIError` with a `kind` ended the run (M2 D10)."""


def initial_state(user_input: str) -> AgentState:
    return {
        "turns": [UserTurn(text=user_input)],
        "model_calls": 0,
        "final_text": None,
        "failure": None,
        "provider_failure": None,
    }


def recursion_limit(max_steps: int) -> int:
    """LangGraph's super-step limit, set so that our own step limit always fires first."""
    return 2 * max_steps + 5


def turn_json(turn: Turn) -> dict[str, Any]:
    if isinstance(turn, UserTurn):
        return {"type": "user", "text": turn.text}
    if isinstance(turn, AssistantTurn):
        return {
            "type": "assistant",
            "text": turn.text,
            "tool_calls": [asdict(call) for call in turn.tool_calls],
        }
    return {"type": "tool_results", "results": [asdict(result) for result in turn.results]}


def _fail(span: Span, message: str) -> None:
    span.set_status(Status(StatusCode.ERROR, message))


def _outcome(response: ModelResponse) -> tuple[str | None, str | None]:
    """`(final_text, failure)` for a response; both `None` means call the tools (D7)."""
    reason = response.stop_reason
    if reason == "tool_use":
        if not response.tool_calls:
            return None, "model stopped for tool_use without calling a tool"
        return None, None
    if reason in COMPLETED_STOP_REASONS:
        return response.text or "", None
    if reason == "max_tokens":
        return None, "answer truncated at max_tokens"
    return None, f"unexpected stop reason: {reason}"


def build_graph(
    provider: Provider, config: AgentConfig, tracer: Tracer
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    max_steps = config.params.max_steps
    tools = {tool.spec.name: tool for tool in config.tools}
    tool_specs = [tool.spec for tool in config.tools]

    async def model(state: AgentState) -> dict[str, Any]:
        if state["model_calls"] >= max_steps:
            return {"failure": f"step limit reached ({max_steps} model calls)"}
        request = ModelRequest(
            model=config.model,
            system=config.system_prompt,
            turns=list(state["turns"]),
            tools=tool_specs,
            params=dict(config.params.model_params),
        )
        # The provider runs with this span current, so the adapter's retry events land on it.
        with tracer.start_as_current_span(
            f"chat {config.model}", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attributes(
                {
                    attr.SPAN_KIND: "llm",
                    attr.GEN_AI_OPERATION: "chat",
                    attr.GEN_AI_SYSTEM: provider.name,
                    attr.GEN_AI_REQUEST_MODEL: config.model,
                    attr.INPUT: json.dumps(
                        {
                            "system": request.system,
                            "turns": [turn_json(turn) for turn in request.turns],
                            "tools": [asdict(spec) for spec in request.tools],
                            "params": request.params,
                        }
                    ),
                }
            )
            calls = state["model_calls"] + 1
            try:
                response = await provider.complete(request)
            except ProviderError as exc:
                error = f"provider error: {exc}"
                _fail(span, error)
                return {
                    "model_calls": calls,
                    "failure": error,
                    "provider_failure": getattr(exc, "kind", None),
                }
            span.set_attributes(
                {
                    attr.GEN_AI_RESPONSE_MODEL: response.response_model,
                    attr.GEN_AI_INPUT_TOKENS: response.input_tokens,
                    attr.GEN_AI_OUTPUT_TOKENS: response.output_tokens,
                    attr.GEN_AI_FINISH_REASONS: [response.stop_reason],
                    attr.LLM_THINKING_BLOCKS: response.thinking_blocks,
                    attr.OUTPUT: json.dumps(
                        {
                            "text": response.text,
                            "tool_calls": [asdict(call) for call in response.tool_calls],
                        }
                    ),
                }
            )
            final_text, failure = _outcome(response)
            if failure is not None:
                _fail(span, failure)
            else:
                span.set_status(Status(StatusCode.OK))
        # Append only: earlier turns, and the raw content with its thinking blocks, are replayed
        # unchanged on every later request (M2.T11).
        turns = [
            *state["turns"],
            AssistantTurn(
                text=response.text,
                tool_calls=response.tool_calls,
                raw_content=response.raw_content,
            ),
        ]
        return {
            "turns": turns,
            "model_calls": calls,
            "final_text": final_text,
            "failure": failure,
        }

    async def run_tools(state: AgentState) -> dict[str, Any]:
        last = state["turns"][-1]
        assert isinstance(last, AssistantTurn)
        # On a failure, earlier results in this turn are not carried: the run has failed, they're
        # on their spans, and the conversation state is discarded.
        results: list[ToolResult] = []
        for call in last.tool_calls:
            with tracer.start_as_current_span(
                f"execute_tool {call.name}", record_exception=False, set_status_on_exception=False
            ) as span:
                span.set_attributes(
                    {
                        attr.SPAN_KIND: "tool",
                        attr.GEN_AI_OPERATION: "execute_tool",
                        attr.GEN_AI_TOOL_NAME: call.name,
                        attr.GEN_AI_TOOL_CALL_ID: call.id,
                        attr.INPUT: json.dumps(call.arguments),
                    }
                )
                tool = tools.get(call.name)
                if tool is None:
                    failure = f"model called unknown tool: {call.name}"
                    _fail(span, failure)
                    return {"failure": failure}
                try:
                    content = await tool.func(call.arguments)
                except Exception as exc:
                    failure = f"tool {call.name} failed: {exc}"
                    _fail(span, failure)
                    return {"failure": failure}
                span.set_attribute(attr.OUTPUT, json.dumps(content))
                span.set_status(Status(StatusCode.OK))
            results.append(ToolResult(tool_call_id=call.id, content=content))
        return {"turns": [*state["turns"], ToolResultsTurn(results=tuple(results))]}

    def after_model(state: AgentState) -> str:
        if state["failure"] is None and state["final_text"] is None:
            return "tools"
        return END

    def after_tools(state: AgentState) -> str:
        return END if state["failure"] is not None else "model"

    graph = StateGraph(AgentState)
    graph.add_node("model", model)
    graph.add_node("tools", run_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", after_model, ["tools", END])
    graph.add_conditional_edges("tools", after_tools, ["model", END])
    return graph.compile()
