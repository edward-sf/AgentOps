"""`make smoke`: one real run of the seeded `weather-bot` v1, end to end (spec D3, 6.6).

It calls the real Anthropic API (two model calls), so it needs `ANTHROPIC_API_KEY`, and no test,
`make check` step or CI job runs it. It drives the app in process through `POST /api/v1/runs` and
`GET /api/v1/traces/{id}` against `DATABASE_URL`, then prints the span tree rebuilt from the
database. Exit codes: 0 ok, 1 the run didn't complete with a tool span, 2 no key.
"""

import asyncio
import sys
from datetime import datetime
from typing import Any, TextIO

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.api.deps import ProviderFactory, get_provider_factory
from app.config import Settings, get_settings
from app.main import create_app

AGENT_NAME = "weather-bot"
VERSION = 1
INPUT = "Find the weather in Seattle"
RESEED_HINT = (
    "hint: this database was seeded before M1; reset the dev DB volume, "
    "then run 'make db migrate seed' (see README)"
)


class SmokeError(Exception):
    pass


def _ok(response: Any, what: str) -> Any:
    if response.status_code >= 300:
        raise SmokeError(f"{what}: HTTP {response.status_code} {response.text}")
    return response.json()


async def _find_agent_id(client: AsyncClient) -> str:
    offset = 0
    while True:
        page = _ok(
            await client.get("/api/v1/agents", params={"limit": 200, "offset": offset}),
            "list agents",
        )
        for agent in page["items"]:
            if agent["name"] == AGENT_NAME:
                return str(agent["id"])
        offset += len(page["items"])
        if not page["items"] or offset >= page["total"]:
            raise SmokeError(f"agent {AGENT_NAME!r} not found; run 'make seed'")


def _duration_ms(span: dict[str, Any]) -> str:
    if span["ended_at"] is None:
        return "?"
    started = datetime.fromisoformat(span["started_at"])
    ended = datetime.fromisoformat(span["ended_at"])
    return f"{(ended - started).total_seconds() * 1000:.0f}"


def span_lines(spans: list[dict[str, Any]], depth: int = 0) -> list[str]:
    """One line per span: indentation, kind, name, status, duration, and tokens for `llm`."""
    lines: list[str] = []
    for span in spans:
        line = (
            f"{'  ' * depth}{span['kind']} {span['name']} {span['status']} {_duration_ms(span)}ms"
        )
        if span["kind"] == "llm":
            attrs = span["attributes"]
            line += (
                f" in={attrs.get('gen_ai.usage.input_tokens', '?')}"
                f" out={attrs.get('gen_ai.usage.output_tokens', '?')}"
            )
        lines.append(line)
        lines.extend(span_lines(span["children"], depth + 1))
    return lines


def _kinds(spans: list[dict[str, Any]]) -> set[str]:
    kinds: set[str] = set()
    for span in spans:
        kinds.add(span["kind"])
        kinds |= _kinds(span["children"])
    return kinds


async def smoke(client: AsyncClient, out: TextIO = sys.stdout, err: TextIO = sys.stderr) -> int:
    """Run the check through `client`. Returns the exit code."""
    try:
        agent_id = await _find_agent_id(client)
        version = _ok(
            await client.get(f"/api/v1/agents/{agent_id}/versions/{VERSION}"),
            f"get {AGENT_NAME} v{VERSION}",
        )
        r = await client.post(
            "/api/v1/runs", json={"agent_version_id": version["id"], "input": INPUT}
        )
        if r.status_code == 422 and "unsupported parameter" in str(r.json().get("detail")):
            print(f"run: HTTP 422 {r.text}", file=err)
            print(RESEED_HINT, file=err)
            return 1
        run = _ok(r, "run")
        trace = _ok(await client.get(f"/api/v1/traces/{run['trace_id']}"), "get trace")
    except SmokeError as exc:
        print(str(exc), file=err)
        return 1

    print(f"trace {trace['id']} {trace['status']}", file=out)
    for line in span_lines(trace["spans"]):
        print(line, file=out)
    if trace["status"] != "completed":
        print(f"run did not complete: {trace['error']}", file=err)
        return 1
    if "tool" not in _kinds(trace["spans"]):
        print("run completed without a tool span", file=err)
        return 1
    return 0


async def run(
    settings: Settings | None = None, provider_factory: ProviderFactory | None = None
) -> int:
    """Build the app against `DATABASE_URL` and run the check. Returns the exit code."""
    settings = settings or get_settings()
    key = settings.anthropic_api_key
    if provider_factory is None and not (key and key.get_secret_value()):
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2
    app = create_app(settings)
    if provider_factory is not None:
        factory = provider_factory
        app.dependency_overrides[get_provider_factory] = lambda: factory
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app), base_url="http://smoke", timeout=300) as client,
    ):
        return await smoke(client)


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
