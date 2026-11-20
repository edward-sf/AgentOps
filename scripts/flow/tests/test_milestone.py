"""Issue-body rendering, PR-body section replacement, roll-up, and milestone-start."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from flow import board, gh, milestone
from flow.plan import Task, parse_plan

PLAN_URL = "https://example.test/plan.md#m9t2-second-task"
START_FILES = {
    "docs/plans/2026-m9-x.md": Path("/repo/docs/plans/2026-m9-x.md"),
    "docs/plans/2026-roadmap.md": Path("/repo/docs/plans/2026-roadmap.md"),
}

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
        self.inputs: dict[str, str | None] = {}
        self.statuses: dict[int, str] = {int(i["number"]): "Ready" for i in existing}
        self.open_prs: list[dict[str, Any]] = [{"number": 10, "body": ""}]
        self.merged: list[dict[str, Any]] = []
        self.ahead = 3
        self.branch_exists = True
        self.git_calls: list[list[str]] = []

    def run(
        self, args: list[str], *, input: str | None = None, allow: tuple[int, ...] = (0,)
    ) -> str:
        self.calls.append(args)
        self.inputs[" ".join(args[:3])] = input
        if args[:2] == ["issue", "create"]:
            return "https://github.com/edward-sf/AgentOps/issues/77\n"
        return ""

    def json(self, args: list[str], *, allow: tuple[int, ...] = (0,)) -> Any:
        self.calls.append(args)
        if args[0] == "api" and "compare" in args[1]:
            return {"ahead_by": self.ahead}
        if args[0] == "api":
            return [{"number": 5, "title": "M9 - Test milestone"}]
        if args[:2] == ["issue", "list"]:
            return self.existing
        if args[:2] == ["project", "item-list"]:
            return {
                "items": [
                    {"content": {"number": n}, "status": st} for n, st in self.statuses.items()
                ]
            }
        if args[:2] == ["pr", "list"]:
            return self.merged if "merged" in args else self.open_prs
        raise AssertionError(args)

    def git(self, args: list[str], env: Mapping[str, str] | None = None) -> str:
        self.git_calls.append(args)
        if args[0] in ("read-tree", "update-index", "write-tree"):
            assert env is not None and "GIT_INDEX_FILE" in env  # never the user's index
        return {"hash-object": "blobsha", "write-tree": "treesha", "commit-tree": "commitsha"}.get(
            args[0], ""
        )

    def writes(self) -> list[list[str]]:
        return [
            c
            for c in self.calls
            if c[:2] in (["issue", "create"], ["issue", "edit"], ["pr", "create"], ["pr", "edit"])
        ]


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
        monkeypatch.setattr(milestone, "remote_branch_exists", lambda b: f.branch_exists)
        monkeypatch.setattr(milestone, "git", f.git)
        return f

    return install


def run_start(f: FakeGh, dry: bool = False) -> list[str]:
    ts = tasks()
    return milestone.milestone_start(
        9,
        ts,
        lambda t: f"{PLAN_URL}-{t.number}",
        dry_run=dry,
        pr_template="# M<N> — <name>\n",
        start_files=START_FILES,
    )


def test_milestone_start_is_idempotent(wire: Callable[[list[dict[str, Any]]], FakeGh]) -> None:
    f = wire(existing_issues(tasks()))
    out = run_start(f)
    assert out[:2] == ["unchanged M9.T1", "unchanged M9.T2"]
    assert f.writes() == []
    assert not any(c[0] == "board" for c in f.calls)


def test_restart_preserves_ticked_boxes(wire: Callable[[list[dict[str, Any]]], FakeGh]) -> None:
    existing = existing_issues(tasks())
    existing[0]["body"] = existing[0]["body"].replace("- [ ] Criterion one", "- [x] Criterion one")
    existing[1]["body"] = existing[1]["body"].replace("- [ ] Only criterion", "- [X] Only criterion")
    existing[1]["body"] = existing[1]["body"].replace("Do the second thing.", "Old goal.")
    f = wire(existing)
    out = run_start(f)
    assert out[:2] == ["unchanged M9.T1", "update M9.T2"]  # a tick alone is not drift
    assert [c[:3] for c in f.writes()] == [["issue", "edit", "22"]]
    body = f.inputs["issue edit 22"] or ""
    assert "- [X] Only criterion" in body and "Do the second thing." in body


def test_ticks_kept_only_where_the_criterion_text_matches() -> None:
    new = "### Acceptance criteria\n- [ ] Criterion one\n- [ ] Reworded two\n  - [ ] nested\n"
    old = "### Acceptance criteria\n- [x] Criterion one  \n- [x] Criterion two\n  - [x] nested\n"
    assert milestone.keep_ticks(new, old) == (
        "### Acceptance criteria\n- [x] Criterion one\n- [ ] Reworded two\n  - [x] nested\n"
    )
    assert milestone.keep_ticks(new, "") == new


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


def test_branch_missing_pushes_empty_commit_then_creates_pr(
    wire: Callable[[list[dict[str, Any]]], FakeGh],
) -> None:
    f = wire(existing_issues(tasks()))
    f.branch_exists = False
    f.open_prs = []
    out = run_start(f)
    assert out[-2].startswith("branch create") and out[-1].startswith("pr create")
    assert [c[0] for c in f.git_calls] == [
        "fetch",
        "read-tree",
        "hash-object",
        "update-index",
        "hash-object",
        "update-index",
        "write-tree",
        "commit-tree",
        "push",
    ]
    assert f.git_calls[1] == ["read-tree", "origin/main^{tree}"]
    assert f.git_calls[3] == [
        "update-index",
        "--add",
        "--cacheinfo",
        "100644,blobsha,docs/plans/2026-m9-x.md",
    ]
    assert f.git_calls[5][3].endswith(",docs/plans/2026-roadmap.md")
    assert f.git_calls[7] == [
        "commit-tree",
        "treesha",
        "-p",
        "origin/main",
        "-m",
        "chore: start milestone m9",
    ]
    assert f.git_calls[8] == ["push", "origin", "commitsha:refs/heads/milestone/m9"]
    (create,) = [c for c in f.calls if c[:2] == ["pr", "create"]]
    assert "--draft" in create and "milestone/m9" in create


def test_branch_exists_pr_missing_creates_pr(
    wire: Callable[[list[dict[str, Any]]], FakeGh],
) -> None:
    f = wire(existing_issues(tasks()))
    f.open_prs = []
    out = run_start(f)
    assert out[-2:] == ["branch exists milestone/m9", "pr create (draft) milestone/m9 -> main"]
    assert f.git_calls == []
    assert len([c for c in f.calls if c[:2] == ["pr", "create"]]) == 1


def test_branch_exists_with_no_commits_refuses_cleanly(
    wire: Callable[[list[dict[str, Any]]], FakeGh],
) -> None:
    f = wire(existing_issues(tasks()))
    f.open_prs = []
    f.ahead = 0
    with pytest.raises(milestone.MilestoneError, match="no commits ahead"):
        run_start(f)
    assert [c for c in f.calls if c[:2] == ["pr", "create"]] == []


def test_existing_issue_off_board_is_added_as_ready(
    wire: Callable[[list[dict[str, Any]]], FakeGh],
) -> None:
    f = wire(existing_issues(tasks()))
    f.statuses = {22: "In progress"}  # #21 is off the board; #22 keeps its status
    out = run_start(f)
    assert out[:3] == ["board M9.T1", "unchanged M9.T1", "unchanged M9.T2"]
    assert [c for c in f.calls if c[0] == "board"] == [["board", "21", "Ready"]]


def test_unreadable_board_changes_nothing(
    wire: Callable[[list[dict[str, Any]]], FakeGh], monkeypatch: pytest.MonkeyPatch
) -> None:
    f = wire(existing_issues(tasks()))
    monkeypatch.setattr(board, "try_statuses", lambda: None)
    run_start(f)
    assert not any(c[0] == "board" for c in f.calls)


def test_replace_section_normalises_crlf_and_handles_eof() -> None:
    out = milestone.replace_section("## A\r\nx\r\n\r\n## B\r\ny", "B", "z")
    assert out == "## A\nx\n\n## B\nz\n"
    assert milestone.replace_section("## A\nx\n## B", "B", "z") == "## A\nx\n## B\nz\n"


def test_replace_section_ignores_headings_inside_fences() -> None:
    body = "## A\n```\n## B\n```\n\n## B\nold\n```\n## C\n```\n\n## D\nd\n"
    out = milestone.replace_section(body, "B", "new")
    assert out == "## A\n```\n## B\n```\n\n## B\nnew\n\n## D\nd\n"


PR_BODY = (
    "# M9\n\n## Issue PRs in this milestone\n_None yet._\n\n## Closes\nCloses #1\n\n## Rest\nkeep\n"
)


def merged(number: int, body: str, at: str) -> dict[str, Any]:
    return {"number": number, "title": f"feat: {number}", "body": body, "mergedAt": at}


def test_milestone_pr_filters_orders_and_dedupes(
    wire: Callable[[list[dict[str, Any]]], FakeGh],
) -> None:
    f = wire([])
    f.open_prs = [{"number": 10, "body": PR_BODY}]
    f.merged = [
        merged(30, "Part of #4", "2026-01-03T00:00:00Z"),
        merged(31, "no link here", "2026-01-02T00:00:00Z"),
        merged(32, "Part of #3", "2026-01-01T00:00:00Z"),
        merged(33, "Part of #4 again", "2026-01-04T00:00:00Z"),
    ]
    (out,) = milestone.milestone_pr(9, dry_run=True)
    assert "- #32 — feat: 32\n- #30 — feat: 30\n- #33 — feat: 33\n" in out
    assert "#31" not in out
    assert "## Closes\nCloses #3\nCloses #4\n\n## Rest\nkeep" in out
    assert f.writes() == []


def test_milestone_pr_edits_once_and_is_stable(
    wire: Callable[[list[dict[str, Any]]], FakeGh],
) -> None:
    f = wire([])
    f.open_prs = [{"number": 10, "body": PR_BODY}]
    f.merged = [merged(30, "Part of #4", "2026-01-03T00:00:00Z")]
    assert milestone.milestone_pr(9, dry_run=False) == ["updated PR #10"]
    assert [c[:3] for c in f.writes()] == [["pr", "edit", "10"]]
    new = milestone.replace_section(PR_BODY, "Issue PRs in this milestone", "- #30 — feat: 30")
    new = milestone.replace_section(new, "Closes", "Closes #4")
    f.open_prs = [{"number": 10, "body": new.replace("\n", "\r\n")}]  # web-UI CRLF edit
    f.calls.clear()
    assert milestone.milestone_pr(9, dry_run=False) == ["unchanged PR #10"]
    assert f.writes() == []


def test_milestone_pr_errors_without_open_pr(
    wire: Callable[[list[dict[str, Any]]], FakeGh],
) -> None:
    f = wire([])
    f.open_prs = []
    with pytest.raises(milestone.MilestoneError, match="no open milestone PR"):
        milestone.milestone_pr(9, dry_run=False)


def test_truncated_board_read_makes_no_set_status_calls(
    wire: Callable[[list[dict[str, Any]]], FakeGh], monkeypatch: pytest.MonkeyPatch
) -> None:
    f = wire(existing_issues(tasks()))
    f.statuses = {22: "In progress", 99: "Done"}  # exactly the limit; #21 only looks off-board
    monkeypatch.setattr(board, "STATUS_LIMIT", 2)
    assert board.try_statuses() is None
    out = run_start(f)
    assert not any(line.startswith("board") for line in out)
    assert not any(c[0] == "board" for c in f.calls)


def test_dry_run_off_board_reports_but_writes_nothing(
    wire: Callable[[list[dict[str, Any]]], FakeGh],
) -> None:
    f = wire(existing_issues(tasks()))
    f.statuses = {22: "Ready"}
    out = run_start(f, dry=True)
    assert out[0] == "board M9.T1"
    assert not any(c[0] == "board" for c in f.calls) and f.writes() == []


def test_milestone_pr_filters_base(wire: Callable[[list[dict[str, Any]]], FakeGh]) -> None:
    f = wire([])
    f.open_prs = [{"number": 10, "body": PR_BODY}]
    milestone.milestone_pr(9, dry_run=True)
    (merged_call,) = [c for c in f.calls if c[:2] == ["pr", "list"] and "merged" in c]
    assert merged_call[merged_call.index("--base") + 1] == "milestone/m9"
    (open_call,) = [c for c in f.calls if c[:2] == ["pr", "list"] and "open" in c]
    assert open_call[open_call.index("--head") + 1] == "milestone/m9"


def test_untitled_issues_ignored_silently(
    wire: Callable[[list[dict[str, Any]]], FakeGh], capsys: pytest.CaptureFixture[str]
) -> None:
    untitled = [
        {"number": 40, "title": "chore: backlog item", "body": "", "labels": []},
        {"number": 41, "title": "fix flow warning", "body": "", "labels": []},
    ]
    f = wire(existing_issues(tasks()) + untitled)
    out = run_start(f)
    assert "warning" not in capsys.readouterr().err
    assert out[:2] == ["unchanged M9.T1", "unchanged M9.T2"]
    assert f.writes() == []
    assert not any(c[0] == "board" for c in f.calls)
