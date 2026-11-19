from typing import Any

import pytest

from flow.guard import DepState, PrState, merge_blockers, start_blockers
from flow.verdict import format_verdict

SHA = "a" * 40
OLD = "b" * 40


def state(**overrides: Any) -> PrState:
    base: dict[str, Any] = {
        "number": 12,
        "head_sha": SHA,
        "base": "milestone/m0",
        "ci": "success",
        "comments": [format_verdict("PASS", SHA, "")],
        "checkpoint": False,
        "checkpoint_approved": False,
    }
    base.update(overrides)
    return PrState(**base)


def test_merge_allowed_when_green_and_pass_at_head() -> None:
    assert merge_blockers(state(ci="success", comments=[format_verdict("PASS", SHA, "")])) == []


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"ci": "pending"}, "ci is pending"),
        ({"ci": "failure"}, "ci is failure"),
        ({"ci": "missing"}, "ci is missing"),
        ({"comments": [format_verdict("PASS", OLD, "")]}, "no PASS verdict at head"),
        ({"comments": []}, "no PASS verdict at head"),
        (
            {"comments": [format_verdict("PASS", SHA, ""), format_verdict("FAIL", SHA, "")]},
            "latest verdict at head is FAIL",
        ),
        ({"checkpoint": True, "checkpoint_approved": False}, "checkpoint not approved"),
        ({"base": "main"}, "base is not a milestone branch"),
    ],
)
def test_merge_blocked(overrides: dict[str, Any], reason: str) -> None:
    assert reason in merge_blockers(state(**overrides))


def test_approved_checkpoint_is_allowed() -> None:
    assert merge_blockers(state(checkpoint=True, checkpoint_approved=True)) == []


def test_all_blockers_are_reported_together() -> None:
    blockers = merge_blockers(state(ci="failure", base="main", comments=[]))
    assert len(blockers) == 3


def test_start_blocked_by_unmerged_or_unapproved_dependency() -> None:
    assert start_blockers([DepState("M0.T3", "In review", True, False)]) != []
    assert start_blockers([DepState("M0.T3", "Merged to milestone", True, False)]) != []
    assert start_blockers([DepState("M0.T3", "Merged to milestone", True, True)]) == []


def test_start_allows_done_case_insensitively_and_blocks_unknown() -> None:
    assert start_blockers([DepState("M0.T1", "DONE", False, False)]) == []
    assert start_blockers([DepState("M0.T1", "merged to milestone", False, False)]) == []
    assert start_blockers([DepState("M0.T1", None, False, False)]) != []
    assert start_blockers([]) == []


def test_issue_must_itself_be_ready() -> None:
    from flow.guard import own_blockers

    assert own_blockers(20, "Ready") == []
    assert own_blockers(20, "ready") == []
    assert own_blockers(20, "In progress") == ["issue #20 is In progress, not Ready"]
    assert own_blockers(20, "Merged to milestone") != []
    assert own_blockers(20, None) == ["issue #20 is unknown, not Ready"]


def _job(name: str, bucket: str = "pass") -> dict[str, Any]:
    return {"name": name, "bucket": bucket, "workflow": "ci"}


def test_missing_required_job_blocks() -> None:
    from flow.guard import ci_status

    checks = [_job(n) for n in ("backend", "frontend", "flow", "contracts")]
    assert ci_status(checks) == (False, ["docker"])


def test_all_required_jobs_green() -> None:
    from flow.config import REQUIRED_CI_JOBS
    from flow.guard import ci_status

    assert REQUIRED_CI_JOBS == ("backend", "frontend", "flow", "contracts", "docker")
    assert ci_status([_job(n) for n in REQUIRED_CI_JOBS]) == (True, [])


def test_failing_pending_or_foreign_jobs_are_not_green() -> None:
    from flow.config import REQUIRED_CI_JOBS
    from flow.guard import ci_status

    jobs = [_job(n) for n in REQUIRED_CI_JOBS]
    assert ci_status([*jobs[:4], _job("docker", "fail")]) == (False, ["docker"])
    assert ci_status([*jobs[:4], _job("docker", "pending")]) == (False, ["docker"])
    # a same-named job from another workflow does not count
    other = {"name": "docker", "bucket": "pass", "workflow": "release"}
    assert ci_status([*jobs[:4], other]) == (False, ["docker"])
    # an extra failing check also blocks, and is named
    assert ci_status([*jobs, _job("lint-extra", "fail")]) == (False, ["lint-extra"])
    assert ci_status([]) == (False, list(REQUIRED_CI_JOBS))
