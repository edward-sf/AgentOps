"""`make eval`: one real evaluation run of `weather-basics` against `weather-bot` v1 (spec 6.6).

It calls the real Anthropic API (one or more model calls per case, so roughly 50 to 80 for the
26 seeded cases), so it needs `ANTHROPIC_API_KEY`, and no test, `make check` step or CI job
runs it. It drives the app in process against `DATABASE_URL`: it submits the run through
`POST /api/v1/evaluation-runs`, executes it with the worker's `run_once` until the run
finishes, and prints one line per result, then the counts.

It never executes a run it didn't create: `run_once` claims the oldest claimable run, so if
any other run is `pending` or `running` (with a fresh or a stale heartbeat), it lists them on
stderr and exits 1 without submitting. If another worker claims this run, `make eval` waits for
it.

Exit codes: 0 the run `completed` with no `error` result, 1 otherwise (other runs queued
included), 2 no key (checked before anything is submitted).
"""

import asyncio
import sys
from functools import partial
from typing import Any, TextIO

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.providers import provider_for
from app.api.deps import ProviderFactory
from app.cli_support import (
    AGENT_NAME,
    RESEED_HINT,
    VERSION,
    CliError,
    find_id_by_name,
    get_version,
    is_reseed_needed,
    ok,
)
from app.config import Settings, get_settings
from app.main import create_app
from app.worker import run_once

DATASET_NAME = "weather-basics"
TEXT_WIDTH = 80
FINISHED = {"completed", "failed", "cancelled"}
UNFINISHED = ("pending", "running")
QUEUED_HINT = (
    "hint: cancel them with POST /api/v1/evaluation-runs/{id}/cancel, or stop the other worker"
)


def result_line(result: dict[str, Any]) -> str:
    """`case_key status trace_id text`: the first 80 characters of the output or error, on
    one line. A result without a trace shows `-`."""
    text = " ".join((result["output"] or result["error"] or "").split())[:TEXT_WIDTH]
    return f"{result['case_key']} {result['status']:<9} {result['trace_id'] or '-'} {text}"


def counts_line(run: dict[str, Any]) -> str:
    counts = run["counts"]
    stored = counts["completed"] + counts["failed"] + counts["error"]
    total = "?" if run["total_cases"] is None else run["total_cases"]
    line = (
        f"run {run['id']} {run['status']}: {stored}/{total} results "
        f"(completed={counts['completed']} failed={counts['failed']} error={counts['error']})"
    )
    return line + (f": {run['error']}" if run["error"] else "")


async def _unfinished_runs(client: AsyncClient) -> list[dict[str, Any]]:
    """Every `pending` or `running` run, whatever its heartbeat: `run_once` could claim any
    of them, and a fresh `running` one belongs to another worker."""
    runs: list[dict[str, Any]] = []
    for status in UNFINISHED:
        offset = 0
        while True:
            page = ok(
                await client.get(
                    "/api/v1/evaluation-runs",
                    params={"status": status, "limit": 200, "offset": offset},
                ),
                f"list {status} runs",
            )
            runs.extend(page["items"])
            offset += len(page["items"])
            if not page["items"] or offset >= page["total"]:
                break
    return runs


async def _submit(client: AsyncClient) -> dict[str, Any]:
    version = await get_version(client)
    dataset_id = await find_id_by_name(client, "/api/v1/datasets", DATASET_NAME)
    r = await client.post(
        "/api/v1/evaluation-runs",
        json={"agent_version_id": version["id"], "dataset_id": dataset_id},
    )
    if is_reseed_needed(r):
        raise CliError(f"submit run: HTTP 422 {r.text}\n{RESEED_HINT}")
    run: dict[str, Any] = ok(r, "submit run")
    return run


async def _results(client: AsyncClient, run_id: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    while True:
        page = ok(
            await client.get(
                f"/api/v1/evaluation-runs/{run_id}/results",
                params={"limit": 200, "offset": len(results)},
            ),
            "list results",
        )
        results.extend(page["items"])
        if not page["items"] or len(results) >= page["total"]:
            return results


async def _drive(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    provider_factory: ProviderFactory,
    settings: Settings,
    out: TextIO,
) -> int:
    others = await _unfinished_runs(client)
    if others:
        print(
            f"eval: {len(others)} other evaluation run(s) not finished; not submitting, so "
            "that only this run is executed:",
            file=sys.stderr,
        )
        for other in others:
            print(f"  {other['id']} {other['status']} {other['created_at']}", file=sys.stderr)
        print(QUEUED_HINT, file=sys.stderr)
        return 1
    run = await _submit(client)
    run_id = run["id"]
    print(f"run {run_id} queued: {AGENT_NAME} v{VERSION} on {DATASET_NAME}", file=out)
    while run["status"] not in FINISHED:
        claimed = await run_once(
            session_factory=session_factory, provider_factory=provider_factory, settings=settings
        )
        if claimed is None:
            # Another worker holds the run, or it is waiting to be reclaimed.
            await asyncio.sleep(settings.eval_poll_interval_seconds)
        elif str(claimed) != run_id:
            # Only if a run was submitted after the check above.
            print(f"eval: also executed run {claimed}, submitted meanwhile", file=sys.stderr)
        run = ok(await client.get(f"/api/v1/evaluation-runs/{run_id}"), "get run")

    for result in await _results(client, run_id):
        print(result_line(result), file=out)
    print(counts_line(run), file=out)
    return 0 if run["status"] == "completed" and run["counts"]["error"] == 0 else 1


async def evaluate(
    settings: Settings, out: TextIO, *, provider_factory: ProviderFactory | None = None
) -> int:
    """Submit and execute the run, and print its results. Returns the exit code.

    Without `provider_factory` the real provider is used, and the key must be set.
    """
    if provider_factory is None:
        key = settings.anthropic_api_key
        if not (key and key.get_secret_value()):
            print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
            return 2
        provider_factory = partial(provider_for, settings=settings)

    app = create_app(settings)
    engine = create_async_engine(settings.database_url)
    try:
        async with (
            LifespanManager(app),
            AsyncClient(transport=ASGITransport(app), base_url="http://eval") as client,
        ):
            return await _drive(
                client,
                async_sessionmaker(engine, expire_on_commit=False),
                provider_factory,
                settings,
                out,
            )
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        await engine.dispose()


def main() -> None:
    sys.exit(asyncio.run(evaluate(get_settings(), sys.stdout)))


if __name__ == "__main__":
    main()
