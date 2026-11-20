import json
from collections import Counter
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.dataset import TestCaseCreate
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
    assert total == _sample_case_count() and cases[0].key == "case_001"
    assert await get_agent_by_name(db_session, "weather-bot") is not None


def _sample_case_count() -> int:
    data = json.loads(SAMPLE.read_text())
    (dataset,) = [d for d in data["datasets"] if d["name"] == "weather-basics"]
    return len(dataset["test_cases"])


async def test_seed_adds_new_cases_to_old_dataset(db_session: AsyncSession, tmp_path: Path) -> None:
    """A database seeded with the original 3 cases gains the rest on the next seed."""
    data = json.loads(SAMPLE.read_text())
    for dataset in data["datasets"]:
        dataset["test_cases"] = dataset["test_cases"][:3]
    old = tmp_path / "old.json"
    old.write_text(json.dumps(data))
    await seed(db_session, old)

    report = await seed(db_session, SAMPLE)
    assert report.created == _sample_case_count() - 3
    dataset = await get_dataset_by_name(db_session, "weather-basics")
    assert dataset is not None
    _, total = await list_test_cases(db_session, dataset.id, 50, 0)
    assert total == _sample_case_count()


# The fixed tag set for `weather-basics`. `weather` marks a weather question and `tool-use` a case
# expected to call get_weather; the rest name the case's category. A case with none of the
# category tags is a single-city lookup (as case_001 and case_002 were tagged before M2.T7).
_CATEGORY_TAGS = {"multi-city", "unknown-location", "ambiguous", "no-tool", "off-topic"}
_TAGS = _CATEGORY_TAGS | {"weather", "tool-use", "hard"}


def _category(case: TestCaseCreate) -> str:
    categories = _CATEGORY_TAGS.intersection(case.tags)
    assert len(categories) <= 1, f"{case.key} has several category tags: {categories}"
    if categories:
        return categories.pop()
    assert {"weather", "tool-use"} <= set(case.tags), f"{case.key} has no category"
    return "single-city"


def test_sample_dataset_shape() -> None:
    data = json.loads(SAMPLE.read_text())
    (dataset,) = [d for d in data["datasets"] if d["name"] == "weather-basics"]
    cases = [TestCaseCreate.model_validate(c) for c in dataset["test_cases"]]

    assert 24 <= len(cases) <= 28
    keys = [c.key for c in cases]
    assert keys == [f"case_{i:03d}" for i in range(1, len(cases) + 1)]

    for case in cases:
        assert set(case.tags) <= _TAGS, f"{case.key} has tags outside the fixed set"
        assert 1 <= len(case.expected_behavior) <= 4, case.key
    counts = Counter(_category(c) for c in cases)
    for category in _CATEGORY_TAGS | {"single-city"}:
        assert counts[category] >= 3, f"{category}: {counts[category]}"
