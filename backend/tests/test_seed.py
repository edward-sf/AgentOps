from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.seed import seed
from app.services.agents import get_agent_by_name
from app.services.datasets import get_dataset_by_name, list_test_cases

SAMPLE = Path(__file__).resolve().parents[2] / "datasets" / "sample.json"


async def test_seed_is_idempotent(db_session: AsyncSession) -> None:
    first = await seed(db_session, SAMPLE)
    assert first.created > 0
    second = await seed(db_session, SAMPLE)
    assert second.created == 0 and second.existing == first.created

    dataset = await get_dataset_by_name(db_session, "weather-basics")
    assert dataset is not None
    cases, total = await list_test_cases(db_session, dataset.id, 50, 0)
    assert total == 3 and cases[0].key == "case_001"
    assert await get_agent_by_name(db_session, "weather-bot") is not None
