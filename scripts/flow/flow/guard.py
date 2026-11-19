"""Pure merge and start guards. No network access here."""

from dataclasses import dataclass
from typing import Any, Literal

from flow.config import CI_WORKFLOW, REQUIRED_CI_JOBS
from flow.verdict import latest_verdict_for

MERGED_STATUSES = ("merged to milestone", "done")


@dataclass(frozen=True)
class PrState:
    number: int
    head_sha: str
    base: str
    ci: Literal["success", "failure", "pending", "missing"]
    comments: list[str]
    checkpoint: bool
    checkpoint_approved: bool


@dataclass(frozen=True)
class DepState:
    task_id: str
    status: str | None
    checkpoint: bool
    approved: bool


def merge_blockers(state: PrState) -> list[str]:
    blockers: list[str] = []
    if state.ci != "success":
        blockers.append(f"ci is {state.ci}")
    verdict = latest_verdict_for(state.comments, state.head_sha)
    if verdict is None:
        blockers.append("no PASS verdict at head")
    elif verdict.result != "PASS":
        blockers.append("latest verdict at head is FAIL")
    if state.checkpoint and not state.checkpoint_approved:
        blockers.append("checkpoint not approved")
    if not state.base.startswith("milestone/"):
        blockers.append("base is not a milestone branch")
    return blockers


def start_blockers(deps: list[DepState]) -> list[str]:
    blockers: list[str] = []
    for dep in deps:
        if (dep.status or "").lower() not in MERGED_STATUSES:
            blockers.append(
                f"{dep.task_id} is not merged to milestone (status: {dep.status or 'unknown'})"
            )
        elif dep.checkpoint and not dep.approved:
            blockers.append(f"{dep.task_id} is a checkpoint without approval")
    return blockers


def own_blockers(number: int, status: str | None) -> list[str]:
    """The issue itself must be Ready before it can be started."""
    if (status or "").lower() == "ready":
        return []
    return [f"issue #{number} is {status or 'unknown'}, not Ready"]


def ci_status(checks: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Green only when every required `ci` job is present and passed, and no other check failed.

    Returns (green, names): the required jobs that are missing, failing or pending, then any
    other check that failed or was cancelled.
    """
    passed = {
        str(c.get("name"))
        for c in checks
        if c.get("workflow", CI_WORKFLOW) == CI_WORKFLOW and c.get("bucket") == "pass"
    }
    bad = [job for job in REQUIRED_CI_JOBS if job not in passed]
    for c in checks:
        name = str(c.get("name"))
        if c.get("bucket") in ("fail", "cancel") and name not in bad:
            bad.append(name)
    return not bad, bad
