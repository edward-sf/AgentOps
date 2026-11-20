"""`make eval` is tested only with a scripted provider factory and through its no-key refusal.

`evaluate()` builds its own app and engine on `DATABASE_URL`, so the seeded data must really
commit (see `committed_session_factory`).
"""

import io
import re
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.agents.providers.base import ProviderAPIError
from app.config import Settings
from app.eval import evaluate
from app.models import EvaluationRun
from app.seed import seed
from tests.evaluation.test_runner import ByInput

SAMPLE = Path(__file__).resolve().parents[2] / "datasets" / "sample.json"
CASES = 26
UNAVAILABLE_INPUT = "Find the weather in Seattle"
"""`case_001`'s input in the sample dataset."""

LINE = re.compile(r"^(case_\d{3}) (completed|failed|error) +(\S+) (.*)$")


def eval_settings(settings: Settings) -> Settings:
    """No API key: the provider is scripted. Short poll interval, so the loop is quick."""
    return settings.model_copy(
        update={"anthropic_api_key": None, "eval_poll_interval_seconds": 0.01}
    )


async def seeded(factory: Any) -> None:
    async with factory() as session:
        await seed(session, SAMPLE)
        await session.commit()


async def run_count(factory: Any) -> int:
    async with factory() as session:
        return await session.scalar(select(func.count()).select_from(EvaluationRun)) or 0


async def test_eval_ok(settings: Settings, committed_session_factory: Any) -> None:
    await seeded(committed_session_factory)
    provider = ByInput()
    out = io.StringIO()

    code = await evaluate(eval_settings(settings), out, provider_factory=lambda name: provider)

    assert code == 0, out.getvalue()
    lines = out.getvalue().splitlines()
    results = [m for m in map(LINE.match, lines) if m]
    assert [m[1] for m in results] == [f"case_{n:03d}" for n in range(1, CASES + 1)]
    assert {m[2] for m in results} == {"completed"}
    assert all(m[3] != "-" for m in results)
    assert results[0][4] == f"ok {UNAVAILABLE_INPUT}"
    assert all(len(m[4]) <= 80 for m in results)
    assert lines[-1].endswith(
        f"completed: {CASES}/{CASES} results (completed={CASES} failed=0 error=0)"
    )


async def test_eval_error_result_exits_1(
    settings: Settings, committed_session_factory: Any
) -> None:
    await seeded(committed_session_factory)
    provider = ByInput(
        {UNAVAILABLE_INPUT: [ProviderAPIError("overloaded: " + "x" * 200, kind="unavailable")]}
    )
    out = io.StringIO()

    code = await evaluate(eval_settings(settings), out, provider_factory=lambda name: provider)

    assert code == 1
    lines = out.getvalue().splitlines()
    first = LINE.match(lines[1])
    assert first is not None
    assert first[1] == "case_001"
    assert first[2] == "error"
    assert first[3] != "-"  # a provider failure still stores the trace
    assert "overloaded: xxx" in first[4]
    assert len(first[4]) == 80
    assert lines[-1].endswith(f"completed: {CASES}/{CASES} results (completed=25 failed=0 error=1)")


async def test_eval_no_key_exits_2(
    settings: Settings, committed_session_factory: Any, capsys: Any
) -> None:
    await seeded(committed_session_factory)

    code = await evaluate(eval_settings(settings), io.StringIO())

    assert code == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err
    assert await run_count(committed_session_factory) == 0
