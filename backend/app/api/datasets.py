import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Response

from app.api.deps import SessionDep
from app.schemas.common import Page, PageParams
from app.schemas.dataset import (
    DatasetCreate,
    DatasetRead,
    DatasetUpdate,
    TestCaseCreate,
    TestCaseRead,
    TestCaseUpdate,
)
from app.services import datasets as svc

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])


@router.post("", status_code=201)
async def create_dataset(data: DatasetCreate, session: SessionDep) -> DatasetRead:
    return DatasetRead.model_validate(await svc.create_dataset(session, data))


@router.get("")
async def list_datasets(session: SessionDep, page: PageParams) -> Page[DatasetRead]:
    items, total = await svc.list_datasets(session, page.limit, page.offset)
    return Page[DatasetRead](
        items=[DatasetRead.model_validate(i) for i in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{dataset_id}")
async def get_dataset(dataset_id: uuid.UUID, session: SessionDep) -> DatasetRead:
    return DatasetRead.model_validate(await svc.get_dataset(session, dataset_id))


@router.patch("/{dataset_id}")
async def update_dataset(
    dataset_id: uuid.UUID, data: DatasetUpdate, session: SessionDep
) -> DatasetRead:
    return DatasetRead.model_validate(await svc.update_dataset(session, dataset_id, data))


@router.delete("/{dataset_id}", status_code=204)
async def delete_dataset(dataset_id: uuid.UUID, session: SessionDep) -> Response:
    await svc.delete_dataset(session, dataset_id)
    return Response(status_code=204)


@router.post("/{dataset_id}/test-cases", status_code=201)
async def create_test_case(
    dataset_id: uuid.UUID, data: TestCaseCreate, session: SessionDep
) -> TestCaseRead:
    return TestCaseRead.model_validate(await svc.create_test_case(session, dataset_id, data))


@router.post("/{dataset_id}/test-cases:bulk", status_code=201)
async def bulk_create_test_cases(
    dataset_id: uuid.UUID,
    items: Annotated[list[TestCaseCreate], Body(min_length=1, max_length=1000)],
    session: SessionDep,
) -> list[TestCaseRead]:
    cases = await svc.bulk_create_test_cases(session, dataset_id, items)
    return [TestCaseRead.model_validate(c) for c in cases]


@router.get("/{dataset_id}/test-cases")
async def list_test_cases(
    dataset_id: uuid.UUID, session: SessionDep, page: PageParams
) -> Page[TestCaseRead]:
    items, total = await svc.list_test_cases(session, dataset_id, page.limit, page.offset)
    return Page[TestCaseRead](
        items=[TestCaseRead.model_validate(i) for i in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{dataset_id}/test-cases/{case_id}")
async def get_test_case(
    dataset_id: uuid.UUID, case_id: uuid.UUID, session: SessionDep
) -> TestCaseRead:
    return TestCaseRead.model_validate(await svc.get_test_case(session, dataset_id, case_id))


@router.patch("/{dataset_id}/test-cases/{case_id}")
async def update_test_case(
    dataset_id: uuid.UUID, case_id: uuid.UUID, data: TestCaseUpdate, session: SessionDep
) -> TestCaseRead:
    return TestCaseRead.model_validate(
        await svc.update_test_case(session, dataset_id, case_id, data)
    )


@router.delete("/{dataset_id}/test-cases/{case_id}", status_code=204)
async def delete_test_case(
    dataset_id: uuid.UUID, case_id: uuid.UUID, session: SessionDep
) -> Response:
    await svc.delete_test_case(session, dataset_id, case_id)
    return Response(status_code=204)
