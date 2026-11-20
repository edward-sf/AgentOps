"""The evaluation worker (M2 spec D1, D5, 6.2 step 7). Every model call is scripted.

Data really commits (see `committed_session_factory`). Shutdown tests synchronise on events,
not sleeps.
"""

import asyncio
import logging
import os
import signal
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.evaluation import runner
from app.main import create_app
from app.models import EvaluationResult, EvaluationRun
from app.worker import run_once, serve
from tests.agents.fakes import end_turn
from tests.evaluation.test_runner import ByInput, question
from tests.factories import make_agent_version, make_dataset, make_evaluation_run

Factory = async_sessionmaker[AsyncSession]
BACKEND_DIR = Path(__file__).resolve().parents[2]
WAIT = 10.0
"""Seconds a test waits before failing instead of hanging."""


@pytest.fixture
def settings() -> Settings:
    """The test database, a short poll so `serve` tests are quick, and no API key: every
    provider is scripted."""
    base = Settings()
    return base.model_copy(
        update={
            "database_url": base.test_database_url,
            "anthropic_api_key": None,
            "eval_concurrency": 2,
            "eval_poll_interval_seconds": 0.01,
        }
    )


async def make_pending_run(factory: Factory, *, cases: int = 2) -> uuid.UUID:
    async with factory() as s:
        version = await make_agent_version(s)
        dataset = await make_dataset(s, cases=cases)
        run = await make_evaluation_run(s, version=version, dataset=dataset)
        await s.commit()
        return run.id


async def load_run(factory: Factory, run_id: uuid.UUID) -> EvaluationRun:
    async with factory() as s:
        run = await s.get(EvaluationRun, run_id)
        assert run is not None
        return run


async def result_count(factory: Factory, run_id: uuid.UUID) -> int:
    async with factory() as s:
        count = await s.scalar(
            select(func.count())
            .select_from(EvaluationResult)
            .where(EvaluationResult.evaluation_run_id == run_id)
        )
        return count or 0


def provider_factory(provider: Any) -> Any:
    return lambda name: provider


async def test_run_once_idle_returns_none(
    committed_session_factory: Factory, settings: Settings
) -> None:
    claimed = await run_once(
        session_factory=committed_session_factory,
        provider_factory=provider_factory(ByInput()),
        settings=settings,
    )
    assert claimed is None


async def test_run_once_executes_oldest(
    committed_session_factory: Factory, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    oldest = await make_pending_run(committed_session_factory)
    newer = await make_pending_run(committed_session_factory)

    with caplog.at_level(logging.INFO, logger="app.worker"):
        claimed = await asyncio.wait_for(
            run_once(
                session_factory=committed_session_factory,
                provider_factory=provider_factory(ByInput()),
                settings=settings,
            ),
            timeout=WAIT,
        )

    assert claimed == oldest
    run = await load_run(committed_session_factory, oldest)
    assert run.status == "completed"
    assert await result_count(committed_session_factory, oldest) == 2
    assert (await load_run(committed_session_factory, newer)).status == "pending"
    messages = [r.getMessage() for r in caplog.records if r.name == "app.worker"]
    assert any("claimed" in m and str(oldest) in m for m in messages)
    assert any(
        str(oldest) in m and "completed" in m and "2/2" in m and "completed=2" in m
        for m in messages
    )


async def run_serve(
    factory: Factory, settings: Settings, provider: Any, stop: asyncio.Event
) -> None:
    await asyncio.wait_for(
        serve(
            session_factory=factory,
            provider_factory=provider_factory(provider),
            settings=settings,
            stop=stop,
        ),
        timeout=WAIT,
    )


async def test_serve_stops_on_event(
    committed_session_factory: Factory,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`stop` set after one run: `serve` returns without claiming the next one."""
    first = await make_pending_run(committed_session_factory)
    second = await make_pending_run(committed_session_factory)
    stop = asyncio.Event()
    real_execute = runner.execute_run

    async def execute_then_stop(run_id: uuid.UUID, **kwargs: Any) -> None:
        await real_execute(run_id, **kwargs)
        stop.set()

    monkeypatch.setattr("app.worker.execute_run", execute_then_stop)
    with caplog.at_level(logging.INFO, logger="app.worker"):
        await run_serve(committed_session_factory, settings, ByInput(), stop)

    assert (await load_run(committed_session_factory, first)).status == "completed"
    assert (await load_run(committed_session_factory, second)).status == "pending"
    messages = [r.getMessage() for r in caplog.records if r.name == "app.worker"]
    assert any("started" in m for m in messages)
    assert any("stopped" in m for m in messages)


async def test_serve_idles_until_stopped(
    committed_session_factory: Factory, settings: Settings
) -> None:
    """With nothing to claim it polls, and returns promptly when `stop` is set."""
    stop = asyncio.Event()
    task = asyncio.create_task(run_serve(committed_session_factory, settings, ByInput(), stop))
    await asyncio.sleep(0.05)  # a few poll intervals
    assert not task.done()
    stop.set()
    await task


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("database went away"),
        ExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("lane failed")]),
    ],
    ids=["plain", "group"],
)
async def test_serve_survives_execute_error(
    committed_session_factory: Factory,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """An exception from `execute_run` is logged with its traceback, the run stays
    `running` (to be reclaimed after the stale timeout) and the next run is processed."""
    broken = await make_pending_run(committed_session_factory)
    good = await make_pending_run(committed_session_factory)
    stop = asyncio.Event()
    real_execute = runner.execute_run
    calls: list[uuid.UUID] = []

    async def flaky_execute(run_id: uuid.UUID, **kwargs: Any) -> None:
        calls.append(run_id)
        if len(calls) == 1:
            raise error
        await real_execute(run_id, **kwargs)
        stop.set()

    monkeypatch.setattr("app.worker.execute_run", flaky_execute)
    with caplog.at_level(logging.INFO, logger="app.worker"):
        await run_serve(committed_session_factory, settings, ByInput(), stop)

    assert calls == [broken, good]
    assert (await load_run(committed_session_factory, broken)).status == "running"
    assert (await load_run(committed_session_factory, good)).status == "completed"
    [record] = [r for r in caplog.records if r.name == "app.worker" and r.exc_info]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None and record.exc_info[1] is error
    assert f"evaluation run {broken}" in getattr(error, "__notes__", [])


async def test_serve_survives_claim_error(
    committed_session_factory: Factory,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A database error while claiming is logged, and the worker polls again."""
    run_id = await make_pending_run(committed_session_factory)
    stop = asyncio.Event()
    real_claim = runner.queue.claim_next
    attempts = 0

    async def flaky_claim(session: AsyncSession, **kwargs: Any) -> uuid.UUID | None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("connection refused")
        claimed = await real_claim(session, **kwargs)
        if claimed is None:
            stop.set()
        return claimed

    monkeypatch.setattr("app.worker.queue.claim_next", flaky_claim)
    with caplog.at_level(logging.INFO, logger="app.worker"):
        await run_serve(committed_session_factory, settings, ByInput(), stop)

    assert (await load_run(committed_session_factory, run_id)).status == "completed"
    assert any(r.exc_info for r in caplog.records if r.name == "app.worker")


async def test_stop_lets_in_flight_cases_finish_and_leaves_run_running(
    committed_session_factory: Factory, settings: Settings
) -> None:
    """On shutdown the worker starts no new case, stores the in-flight ones' results, and
    returns without finishing the run, so it is reclaimed and resumed."""
    run_id = await make_pending_run(committed_session_factory, cases=4)
    settings = settings.model_copy(update={"eval_concurrency": 1})
    stop = asyncio.Event()
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> Any:
        in_flight.set()
        await release.wait()
        return end_turn("held answer")

    provider = ByInput({question(1): [hold]})
    task = asyncio.create_task(run_serve(committed_session_factory, settings, provider, stop))
    await asyncio.wait_for(in_flight.wait(), timeout=WAIT)
    stop.set()
    await asyncio.sleep(0)  # let the runner see the stop before the case ends
    release.set()
    await task

    run = await load_run(committed_session_factory, run_id)
    assert run.status == "running"
    assert run.completed_at is None
    assert await result_count(committed_session_factory, run_id) == 1
    assert provider.inputs == [question(1)]


@pytest.fixture
async def committed_client(
    committed_session_factory: Factory, settings: Settings
) -> AsyncIterator[AsyncClient]:
    """The app on the test database with real commits, so the worker sees the POSTed run."""
    application = create_app(settings)
    async with LifespanManager(application):
        transport = ASGITransport(application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_end_to_end_via_api(
    committed_session_factory: Factory, committed_client: AsyncClient, settings: Settings
) -> None:
    async with committed_session_factory() as s:
        version = await make_agent_version(s)
        dataset = await make_dataset(s, cases=3)
        await s.commit()

    created = await committed_client.post(
        "/api/v1/evaluation-runs",
        json={"agent_version_id": str(version.id), "dataset_id": str(dataset.id)},
    )
    assert created.status_code == 202, created.text
    assert created.json()["status"] == "pending"

    claimed = await asyncio.wait_for(
        run_once(
            session_factory=committed_session_factory,
            provider_factory=provider_factory(ByInput()),
            settings=settings,
        ),
        timeout=WAIT,
    )
    assert str(claimed) == created.json()["id"]

    got = await committed_client.get(f"/api/v1/evaluation-runs/{claimed}")
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "completed"
    assert body["total_cases"] == 3
    assert body["counts"] == {"completed": 3, "failed": 0, "error": 0}


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
async def test_main_exits_zero_on_signal(
    committed_session_factory: Factory, settings: Settings, sig: signal.Signals
) -> None:
    """`python -m app.worker` against the (empty) test database, with the API key blanked
    so `.env` can't supply a real one: it logs its start, and exits 0 on the signal."""
    env = {
        **os.environ,
        "DATABASE_URL": settings.test_database_url,
        "ANTHROPIC_API_KEY": "",
        "EVAL_POLL_INTERVAL_SECONDS": "0.05",
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.worker",
        cwd=BACKEND_DIR,
        env=env,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stderr is not None
    try:
        started = await asyncio.wait_for(proc.stderr.readline(), timeout=WAIT)
        while b"worker started" not in started:
            assert started, "the worker exited before it started"
            started = await asyncio.wait_for(proc.stderr.readline(), timeout=WAIT)
        proc.send_signal(sig)
        rest = await asyncio.wait_for(proc.stderr.read(), timeout=WAIT)
        code = await asyncio.wait_for(proc.wait(), timeout=WAIT)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    assert code == 0, rest.decode()
    assert b"worker stopped" in rest
