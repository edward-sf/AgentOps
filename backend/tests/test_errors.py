from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.errors import ConflictError, delete_conflict, integrity_conflict


def _exc(constraint: str | None) -> IntegrityError:
    orig = Exception("violation")
    orig.diag = SimpleNamespace(constraint_name=constraint)  # type: ignore[attr-defined]
    return IntegrityError("stmt", {}, orig)


def test_known_unique_maps_to_conflict() -> None:
    err = integrity_conflict(_exc("uq_agents_name"))
    assert isinstance(err, ConflictError) and err.detail == "Agent name already exists"


@pytest.mark.parametrize("name", ["uq_something_new", "ck_agent_versions_provider", None])
def test_unknown_violation_is_reraised(name: str | None) -> None:
    exc = _exc(name)
    with pytest.raises(IntegrityError) as raised:
        integrity_conflict(exc)
    assert raised.value is exc


def test_insert_time_fk_violation_is_not_a_delete_conflict() -> None:
    exc = _exc("fk_test_cases_dataset_id_datasets")
    with pytest.raises(IntegrityError):
        integrity_conflict(exc)


def test_delete_fk_violation_names_the_entity() -> None:
    err = delete_conflict(_exc("fk_traces_test_case_id_test_cases"), "Test case")
    assert err.detail == "Test case is referenced by evaluation history"


def test_delete_with_unknown_violation_is_reraised() -> None:
    exc = _exc("ck_whatever")
    with pytest.raises(IntegrityError):
        delete_conflict(exc, "Dataset")
