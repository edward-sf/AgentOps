import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConflictError, NotFoundError, delete_conflict, integrity_conflict
from app.models import Dataset, TestCase
from app.schemas.dataset import DatasetCreate, DatasetUpdate, TestCaseCreate, TestCaseUpdate

# Fields that are NOT NULL in the database: an explicit null in a PATCH is ignored.
_NON_NULLABLE_CASE_FIELDS = ("key", "input", "expected_behavior", "tags")


async def create_dataset(session: AsyncSession, data: DatasetCreate) -> Dataset:
    dataset = Dataset(name=data.name, description=data.description)
    try:
        async with session.begin_nested():
            session.add(dataset)
            await session.flush()
    except IntegrityError as exc:
        raise integrity_conflict(exc) from exc
    await session.refresh(dataset)  # server defaults: id, created_at, updated_at
    return dataset


async def list_datasets(
    session: AsyncSession, limit: int, offset: int
) -> tuple[list[Dataset], int]:
    total = await session.scalar(select(func.count()).select_from(Dataset)) or 0
    result = await session.scalars(
        select(Dataset).order_by(Dataset.created_at, Dataset.id).limit(limit).offset(offset)
    )
    return list(result), total


async def get_dataset(session: AsyncSession, dataset_id: uuid.UUID) -> Dataset:
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise NotFoundError("Dataset not found")
    return dataset


async def get_dataset_by_name(session: AsyncSession, name: str) -> Dataset | None:
    return await session.scalar(select(Dataset).where(Dataset.name == name))


async def update_dataset(
    session: AsyncSession, dataset_id: uuid.UUID, data: DatasetUpdate
) -> Dataset:
    dataset = await get_dataset(session, dataset_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("name") is None:
        changes.pop("name", None)  # name is not nullable
    try:
        async with session.begin_nested():
            for field, value in changes.items():
                setattr(dataset, field, value)
            await session.flush()
    except IntegrityError as exc:
        raise integrity_conflict(exc) from exc
    await session.refresh(dataset)  # updated_at is set by a DB trigger
    return dataset


async def delete_dataset(session: AsyncSession, dataset_id: uuid.UUID) -> None:
    dataset = await get_dataset(session, dataset_id)
    try:
        async with session.begin_nested():
            # Core DELETE: the database cascades to test cases, then RESTRICT guards history.
            await session.execute(delete(Dataset).where(Dataset.id == dataset.id))
    except IntegrityError as exc:
        raise delete_conflict(exc, "Dataset") from exc
    session.expunge(dataset)


async def create_test_case(
    session: AsyncSession, dataset_id: uuid.UUID, data: TestCaseCreate
) -> TestCase:
    await get_dataset(session, dataset_id)
    case = TestCase(dataset_id=dataset_id, **data.model_dump())
    try:
        async with session.begin_nested():
            session.add(case)
            await session.flush()
    except IntegrityError as exc:
        raise integrity_conflict(exc) from exc
    await session.refresh(case)
    return case


async def bulk_create_test_cases(
    session: AsyncSession, dataset_id: uuid.UUID, items: list[TestCaseCreate]
) -> list[TestCase]:
    """Insert every item or none: any conflict rolls back the whole batch."""
    await get_dataset(session, dataset_id)
    seen: set[str] = set()
    for item in items:
        if item.key in seen:
            raise ConflictError(f"Duplicate test case key in request: {item.key}")
        seen.add(item.key)
    cases = [TestCase(dataset_id=dataset_id, **item.model_dump()) for item in items]
    try:
        async with session.begin_nested():
            session.add_all(cases)
            await session.flush()
    except IntegrityError as exc:
        raise integrity_conflict(exc) from exc
    for case in cases:
        await session.refresh(case)
    return cases


async def list_test_cases(
    session: AsyncSession, dataset_id: uuid.UUID, limit: int, offset: int
) -> tuple[list[TestCase], int]:
    await get_dataset(session, dataset_id)
    total = (
        await session.scalar(
            select(func.count()).select_from(TestCase).where(TestCase.dataset_id == dataset_id)
        )
        or 0
    )
    result = await session.scalars(
        select(TestCase)
        .where(TestCase.dataset_id == dataset_id)
        .order_by(TestCase.created_at, TestCase.key)
        .limit(limit)
        .offset(offset)
    )
    return list(result), total


async def get_test_case(
    session: AsyncSession, dataset_id: uuid.UUID, case_id: uuid.UUID
) -> TestCase:
    await get_dataset(session, dataset_id)
    case = await session.scalar(
        select(TestCase).where(TestCase.id == case_id, TestCase.dataset_id == dataset_id)
    )
    if case is None:
        raise NotFoundError("Test case not found")
    return case


async def get_test_case_by_key(
    session: AsyncSession, dataset_id: uuid.UUID, key: str
) -> TestCase | None:
    return await session.scalar(
        select(TestCase).where(TestCase.dataset_id == dataset_id, TestCase.key == key)
    )


async def update_test_case(
    session: AsyncSession, dataset_id: uuid.UUID, case_id: uuid.UUID, data: TestCaseUpdate
) -> TestCase:
    case = await get_test_case(session, dataset_id, case_id)
    changes = data.model_dump(exclude_unset=True)
    for field in _NON_NULLABLE_CASE_FIELDS:
        if changes.get(field, ...) is None:
            changes.pop(field)
    try:
        async with session.begin_nested():
            for field, value in changes.items():
                setattr(case, field, value)
            await session.flush()
    except IntegrityError as exc:
        raise integrity_conflict(exc) from exc
    await session.refresh(case)  # updated_at is set by a DB trigger
    return case


async def delete_test_case(
    session: AsyncSession, dataset_id: uuid.UUID, case_id: uuid.UUID
) -> None:
    case = await get_test_case(session, dataset_id, case_id)
    try:
        async with session.begin_nested():
            await session.execute(delete(TestCase).where(TestCase.id == case.id))
    except IntegrityError as exc:
        raise delete_conflict(exc, "Test case") from exc
    session.expunge(case)
