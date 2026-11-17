"""Issue-body rendering, PR-body section replacement, roll-up, and milestone-start."""

from collections.abc import Callable
from typing import Any

import pytest

from flow import board, gh, milestone
from flow.plan import Task, parse_plan

PLAN_URL = "https://example.test/plan.md#m9t2-second-task"

PLAN = """\
### M9.T1 First task

**Depends on:** none

**Goal:** Do the first thing.
It spans two lines.

Second paragraph is not part of the goal.

**Acceptance criteria**
- [ ] Criterion one
- [ ] Criterion two

---

### M9.T2 Second task `x`

**Depends on:** M9.T1
**Checkpoint**

**Goal:** Do the second thing.

**Acceptance criteria**
- [ ] Only criterion
"""


def tasks() -> list[Task]:
    return parse_plan(PLAN)


def test_render_issue_body_contains_link_goal_and_criteria() -> None:
    body = milestone.render_issue_body(tasks()[0], PLAN_URL)
    assert body.startswith(f"### Plan section\n{PLAN_URL}\n\n### Goal\nDo the first thing.\n")
    assert "It spans two lines." in body and "Second paragraph" not in body
    assert "**Depends on:** none\n" in body
    assert "**Checkpoint:**" not in body
    assert body.endswith("### Acceptance criteria\n- [ ] Criterion one\n- [ ] Criterion two\n")


def test_render_issue_body_marks_checkpoint() -> None:
    body = milestone.render_issue_body(tasks()[1], PLAN_URL)
    assert "**Depends on:** M9.T1\n\n**Checkpoint:** requires author approval" in body


def test_render_issue_body_keeps_nested_criteria_verbatim() -> None:
    plan = "### M9.T3 Nested\n\n**Goal:** G.\n\n**Acceptance criteria**\n- [ ] Refuses when:\n"
    plan += "  - a\n  - b\n- [ ] Second\n\n**Files:**\n- Create: x\n"
    body = milestone.render_issue_body(parse_plan(plan)[0], PLAN_URL)
    assert body.endswith(
        "### Acceptance criteria\n- [ ] Refuses when:\n  - a\n  - b\n- [ ] Second\n"
    )


def test_anchor_is_github_slug() -> None:
    assert milestone.anchor("M0.T2 `flow` CLI: issue loop") == "m0t2-flow-cli-issue-loop"


def test_replace_section_only_touches_named_section() -> None:
    body = "# T\n\n## A\nalpha\n\n## B\nold\nold2\n\n## C\ngamma\n"
    out = milestone.replace_section(body, "B", "new")
    assert out == "# T\n\n## A\nalpha\n\n## B\nnew\n\n## C\ngamma\n"
    assert out.startswith("# T\n\n## A\nalpha\n\n## B\n") and out.endswith("\n## C\ngamma\n")


def test_replace_section_last_section_and_missing_heading() -> None:
    assert milestone.replace_section("## A\nx\n\n## B\ny\n", "B", "z") == "## A\nx\n\n## B\nz\n"
    with pytest.raises(ValueError):
        milestone.replace_section("## A\nx\n", "Nope", "z")


def test_render_rollup_lists_prs_and_closes_lines() -> None:
    prs, closes = milestone.render_rollup([(12, "feat: a"), (14, "feat: b")], [3, 4])
    assert prs == "- #12 — feat: a\n- #14 — feat: b"
    assert closes == "Closes #3\nCloses #4"


class FakeGh:
    def __init__(self, existing: list[dict[str, Any]]) -> None:
        self.existing = existing
        self.calls: list[list[str]] = []

    def run(
        self, args: list[str], *, input: str | None = None, allow: tuple[int, ...] = (0,)
    ) -> str:
        self.calls.append(args)
        if args[:2] == ["issue", "create"]:
            return "https://github.com/edward-sf/AgentOps/issues/77\n"
        return ""

    def json(self, args: list[str], *, allow: tuple[int, ...] = (0,)) -> Any:
        if args[0] == "api":
            return [{"number": 5, "title": "M9 - Test milestone"}]
        if args[:2] == ["issue", "list"]:
            return self.existing
        if args[:2] == ["pr", "list"]:
            return [{"number": 10}]
        raise AssertionError(args)

    def writes(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] in (["issue", "create"], ["issue", "edit"])]


def existing_issues(ts: list[Task]) -> list[dict[str, Any]]:
    return [
        {
            "number": 20 + t.number,
            "title": f"{t.id} {t.title}",
            "body": milestone.render_issue_body(t, f"{PLAN_URL}-{t.number}") + "  \n",
            "labels": [{"name": "checkpoint"}] if t.checkpoint else [{"name": "type:feature"}],
        }
        for t in ts
    ]


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[dict[str, Any]]], FakeGh]:
    def install(existing: list[dict[str, Any]]) -> FakeGh:
        f = FakeGh(existing)
        monkeypatch.setattr(gh, "run", f.run)
        monkeypatch.setattr(gh, "json", f.json)
        monkeypatch.setattr(board, "set_status", lambda n, s: f.calls.append(["board", str(n), s]))
        monkeypatch.setattr(milestone, "remote_branch_exists", lambda b: True)
        return f

    return install


def run_start(f: FakeGh, dry: bool = False) -> list[str]:
    ts = tasks()
    return milestone.milestone_start(
        9, ts, lambda t: f"{PLAN_URL}-{t.number}", dry_run=dry, pr_template="# M<N> — <name>\n"
    )


def test_milestone_start_is_idempotent(wire: Callable[[list[dict[str, Any]]], FakeGh]) -> None:
    f = wire(existing_issues(tasks()))
    out = run_start(f)
    assert out[:2] == ["unchanged M9.T1", "unchanged M9.T2"]
    assert f.writes() == []
    assert not any(c[0] == "board" for c in f.calls)


def test_milestone_start_creates_updates_and_never_resets_status(
    wire: Callable[[list[dict[str, Any]]], FakeGh],
) -> None:
    ts = tasks()
    existing = existing_issues(ts)[:1]  # T2 missing
    existing[0]["body"] += "extra"  # T1 drifted
    f = wire(existing)
    out = run_start(f)
    assert out[:2] == ["update M9.T1", "create M9.T2"]
    (edit,) = [c for c in f.calls if c[:2] == ["issue", "edit"]]
    assert "--add-label" not in edit and "--remove-label" not in edit
    (create,) = [c for c in f.calls if c[:2] == ["issue", "create"]]
    assert "checkpoint" in create and "M9 - Test milestone" in create and "edward-sf" in create
    assert [c for c in f.calls if c[0] == "board"] == [["board", "77", "Ready"]]


def test_milestone_start_dry_run_writes_nothing(
    wire: Callable[[list[dict[str, Any]]], FakeGh],
) -> None:
    f = wire([])
    out = run_start(f, dry=True)
    assert out[:2] == ["create M9.T1", "create M9.T2"]
    assert f.writes() == [] and not any(c[0] == "board" for c in f.calls)
