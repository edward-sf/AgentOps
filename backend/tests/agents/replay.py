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
