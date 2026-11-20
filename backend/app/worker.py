"""`python -m app.worker`: the evaluation worker (M2 spec D1, D5, 6.2 step 7).

It polls `evaluation_runs` as a queue and executes one run at a time with `execute_run`,
which runs up to `EVAL_CONCURRENCY` of its cases at once. When idle it waits
`EVAL_POLL_INTERVAL_SECONDS` before polling again. More than one worker may run: the claim
uses `FOR UPDATE SKIP LOCKED`.

On SIGTERM or SIGINT it stops claiming, lets the current run's in-flight cases finish, and
exits 0 without finishing that run. The run stays `running` and is reclaimed and resumed
after `EVAL_STALE_AFTER_SECONDS`.
"""

import asyncio
import logging
import signal
import uuid
from contextlib import suppress
from datetime import timedelta
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.providers import provider_for
from app.api.deps import ProviderFactory
from app.config import Settings, get_settings
from app.evaluation import queue
from app.evaluation.runner import execute_run
from app.services.evaluation_runs import get_run

logger = logging.getLogger("app.worker")


async def run_once(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    provider_factory: ProviderFactory,
    settings: Settings,
    shutdown: asyncio.Event | None = None,
) -> uuid.UUID | None:
    """Claim the oldest claimable run and execute it. Returns its id, or `None` when idle.

    An exception from `execute_run` propagates with a note naming the run. The run is left
    `running`, so it is reclaimed after the stale timeout. `shutdown` is passed to
    `execute_run`: once set, the run stops without being finished.
    """
    stale_after = timedelta(seconds=settings.eval_stale_after_seconds)
    async with session_factory() as session:
        run_id = await queue.claim_next(session, stale_after=stale_after)
    if run_id is None:
        return None
    logger.info("claimed evaluation run %s", run_id)
    try:
        await execute_run(
            run_id,
            session_factory=session_factory,
            provider_factory=provider_factory,
            concurrency=settings.eval_concurrency,
            stale_after=stale_after,
            shutdown=shutdown,
        )
    except Exception as exc:
        exc.add_note(f"evaluation run {run_id}")
        raise
    await _log_outcome(session_factory, run_id)
    return run_id


async def _log_outcome(
    session_factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> None:
    async with session_factory() as session:
        run = await get_run(session, run_id)
        await session.commit()
    counts = run.counts
    stored = counts.completed + counts.failed + counts.error
    total = "?" if run.total_cases is None else run.total_cases
    ending = "left running, to be reclaimed" if run.status == "running" else f"ended {run.status}"
    logger.info(
        "evaluation run %s %s: %s/%s results (completed=%d failed=%d error=%d)%s",
        run_id,
        ending,
        stored,
        total,
        counts.completed,
        counts.failed,
        counts.error,
        f": {run.error}" if run.error else "",
    )


async def serve(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    provider_factory: ProviderFactory,
    settings: Settings,
    stop: asyncio.Event,
) -> None:
    """Claim and execute runs one at a time until `stop` is set.

    Any exception, from claiming or from `execute_run` (an `ExceptionGroup` included), is
    logged with its traceback and the loop goes on after one poll interval. A run that
    raised stays `running` and is reclaimed after the stale timeout.
    """
    logger.info(
        "evaluation worker started: concurrency %d, stale after %ds, polling every %gs",
        settings.eval_concurrency,
        settings.eval_stale_after_seconds,
        settings.eval_poll_interval_seconds,
    )
    while not stop.is_set():
        try:
            run_id = await run_once(
                session_factory=session_factory,
                provider_factory=provider_factory,
                settings=settings,
                shutdown=stop,
            )
        except Exception:
            logger.exception(
                "evaluation worker: a run or claim raised; a claimed run stays running and is "
                "reclaimed after the stale timeout"
            )
            run_id = None
        if run_id is None:
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.eval_poll_interval_seconds)
    logger.info("evaluation worker stopped")


async def _main(settings: Settings) -> None:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_stop, stop, sig)
    if not settings.anthropic_api_key or not settings.anthropic_api_key.get_secret_value():
        logger.warning("ANTHROPIC_API_KEY is not set: every claimed run will end failed")
    try:
        await serve(
            session_factory=session_factory,
            provider_factory=partial(provider_for, settings=settings),
            settings=settings,
            stop=stop,
        )
    finally:
        await engine.dispose()


def _request_stop(stop: asyncio.Event, sig: signal.Signals) -> None:
    if stop.is_set():
        logger.info("evaluation worker: %s again; still waiting for in-flight cases", sig.name)
        return
    logger.info("evaluation worker: %s; finishing in-flight cases, then stopping", sig.name)
    stop.set()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(_main(get_settings()))


if __name__ == "__main__":
    main()
