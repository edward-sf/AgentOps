"""Replay saved Messages API responses through an in-memory transport.

Responses are returned in order, whatever request is sent (spec D11). No key, no network.
"""

import json
from pathlib import Path
from typing import Any

import anthropic
import httpx2

from app.agents.providers.anthropic import make_anthropic_client

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "anthropic"


def load_fixture(scenario: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_DIR / f"{scenario}.json").read_text())
    return data


def replay_responses(
    *responses: tuple[int, dict[str, Any]],
    requests: list[dict[str, Any]] | None = None,
) -> anthropic.AsyncAnthropic:
    """A client that answers the n-th request with the n-th `(status_code, body)`.

    If `requests` is given, each request's JSON body is appended to it.
    """
    queue = list(responses)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if requests is not None:
            requests.append(json.loads(request.content))
        if not queue:
            raise AssertionError("replay client received more requests than it has responses")
        status_code, body = queue.pop(0)
        return httpx2.Response(status_code, json=body)

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return make_anthropic_client("test-key", http_client=http_client)


def replay_client(
    *fixtures: str, requests: list[dict[str, Any]] | None = None
) -> anthropic.AsyncAnthropic:
    """A client that replays each named fixture's `status_code` and `body`, in order."""
    loaded = [load_fixture(name) for name in fixtures]
    return replay_responses(*((f["status_code"], f["body"]) for f in loaded), requests=requests)


# --- layout-independent expectations (M2.T11) -----------------------------------------------
# Recorded responses may lay out blocks differently from a synthetic fixture: adaptive thinking
# can add thinking blocks to any response, and text can sit before a tool call. Tests derive
# their expectations from a body's own blocks with these helpers instead of fixed positions.

THINKING_TYPES = ("thinking", "redacted_thinking")

LAYOUT_THINKING = {"type": "thinking", "thinking": "", "signature": "EqQBCgIYAhIMlayoutVariant"}
LAYOUT_TEXT = {"type": "text", "text": "Let me check the weather in Seattle."}


def blocks(body: dict[str, Any], *types: str) -> list[dict[str, Any]]:
    """The body's content blocks of the given types, in order."""
    return [b for b in body["content"] if b["type"] in types]


def expected_text(body: dict[str, Any]) -> str | None:
    """What `ModelResponse.text` should be: the text blocks joined, or `None` if there are none."""
    texts = [b["text"] for b in blocks(body, "text")]
    return "".join(texts) if texts else None


def expected_thinking_blocks(body: dict[str, Any]) -> int:
    return len(blocks(body, *THINKING_TYPES))


def wire_form(value: Any) -> Any:
    """`value` without `None` fields, as `model_dump(exclude_none=True)` returns it."""
    if isinstance(value, dict):
        return {k: wire_form(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [wire_form(v) for v in value]
    return value


def assert_thinking_before_tool_use(content: Any) -> None:
    """At least one thinking block, and every one of them before the first `tool_use`."""
    types = [b["type"] for b in content]
    thinking = [i for i, t in enumerate(types) if t in THINKING_TYPES]
    assert thinking, types
    assert "tool_use" in types, types
    assert max(thinking) < types.index("tool_use"), types


def with_content(body: dict[str, Any], content: list[dict[str, Any]]) -> dict[str, Any]:
    return {**body, "content": content}


def thinking_tool_use_layouts() -> dict[str, dict[str, Any]]:
    """The `thinking_tool_use` fixture, and a [thinking, text, tool_use] variant of it."""
    body = load_fixture("thinking_tool_use")["body"]
    thinking = blocks(body, *THINKING_TYPES) or [LAYOUT_THINKING]
    tools = blocks(body, "tool_use")
    return {
        "fixture": body,
        "thinking-text-tool_use": with_content(body, [*thinking, LAYOUT_TEXT, *tools]),
    }


def final_answer_layouts() -> dict[str, dict[str, Any]]:
    """The `final_answer` fixture, and a [thinking, text] variant of it."""
    body = load_fixture("final_answer")["body"]
    return {
        "fixture": body,
        "thinking-text": with_content(body, [LAYOUT_THINKING, *blocks(body, "text")]),
    }


def without_thinking(body: dict[str, Any]) -> dict[str, Any]:
    """`body` with its thinking blocks removed: a response where the model chose not to think."""
    return with_content(body, [b for b in body["content"] if b["type"] not in THINKING_TYPES])
