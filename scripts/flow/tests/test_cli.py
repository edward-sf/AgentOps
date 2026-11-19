import json
import time
from pathlib import Path
from typing import Any

import pytest

from flow import board, cli, gh, milestone
from flow.cli import (
    author_comments,
    ci_from_buckets,
    effective_status,
    heading_anchor,
    milestone_of_title,
    part_of,
    rollup_bucket,
    slug_for,
)
from flow.config import REQUIRED_CI_JOBS
from flow.verdict import format_verdict


def test_slug_and_anchor() -> None:
    title = "M0.T2 `flow` CLI: issue loop"
    assert slug_for(title) == "flow-cli-issue-loop"
    assert slug_for("M0.T3 Data model and first migration") == "data-model-and-first-migration"
    assert heading_anchor("M0.T2 `flow` CLI: issue loop") == "m0t2-flow-cli-issue-loop"


def test_part_of_and_milestone() -> None:
    assert part_of("Part of #12\nmore") == 12
    assert part_of("Closes #12") is None
    assert milestone_of_title("M3.T4 x") == 3
    assert milestone_of_title("fix") is None


def test_ci_from_buckets() -> None:
    assert ci_from_buckets([]) == "missing"
    assert ci_from_buckets(["pass", "skipping"]) == "success"
    assert ci_from_buckets(["pass", "pending"]) == "pending"
    assert ci_from_buckets(["pass", "fail"]) == "failure"
    assert ci_from_buckets(["pass", "weird"]) == "pending"


def test_rollup_bucket() -> None:
    assert rollup_bucket({"status": "COMPLETED", "conclusion": "SUCCESS"}) == "pass"
    assert rollup_bucket({"status": "IN_PROGRESS", "conclusion": ""}) == "pending"
    assert rollup_bucket({"status": "COMPLETED", "conclusion": "FAILURE"}) == "fail"
    assert rollup_bucket({"state": "SUCCESS"}) == "pass"


def test_author_comments_filters_other_authors() -> None:
    comments: list[dict[str, Any]] = [
        {"author": {"login": "edward-sf"}, "body": "a"},
        {"author": {"login": "mallory"}, "body": "b"},
        {"author": None, "body": "c"},
    ]
    assert author_comments(comments) == ["a"]


def test_effective_status_merged_pr_wins_over_stale_board() -> None:
    assert effective_status("In review", True) == "Merged to milestone"
    assert effective_status("Done", True) == "Done"
    assert effective_status("In review", False) == "In review"
    assert effective_status(None, False) is None


# ---------- command glue, with fake gh / git / board seams ----------

SHA = "a" * 40
GREEN = json.dumps([{"name": n, "bucket": "pass", "workflow": "ci"} for n in REQUIRED_CI_JOBS])


class FakeGh:
    def __init__(self) -> None:
        self.pr: dict[str, Any] = {
            "number": 12,
            "title": "feat(x): thing",
            "body": "Part of #2",
            "state": "OPEN",
            "headRefName": "2-thing",
            "headRefOid": SHA,
            "baseRefName": "milestone/m0",
            "comments": [
                {"author": {"login": "edward-sf"}, "body": format_verdict("PASS", SHA, "")}
            ],
        }
        self.checks_seq: list[str] = [GREEN]
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []

    def run(
        self, args: list[str], *, input: str | None = None, allow: tuple[int, ...] = (0,)
    ) -> str:
        self.calls.append(args)
        self.inputs.append(input)
        if args[:2] == ["pr", "checks"]:
            return self.checks_seq.pop(0) if len(self.checks_seq) > 1 else self.checks_seq[0]
        if args[:2] == ["pr", "create"]:
            return "https://github.com/x/y/pull/99\n"
        return ""

    def json(self, args: list[str], *, allow: tuple[int, ...] = (0,)) -> Any:
        if args[:2] == ["pr", "view"]:
            return dict(self.pr)
        if args[:2] == ["issue", "view"]:
            return {"number": 2, "title": "M0.T2 thing", "labels": [], "url": "", "state": "OPEN"}
        raise AssertionError(args)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    f = FakeGh()
    monkeypatch.setattr(gh, "run", f.run)
    monkeypatch.setattr(gh, "json", f.json)
    monkeypatch.setattr(cli, "load_plan", lambda m, base=None: [])
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(cli, "watch_checks", lambda pr: 0)
    return f


def test_pr_gate_retries_when_no_checks(fake: FakeGh, capsys: pytest.CaptureFixture[str]) -> None:
    fake.checks_seq = ["no checks reported on the 'x' branch", "[]", GREEN]
    assert cli.main(["pr-gate", "12"]) == 0
    assert "✓ ci green on PR #12" in capsys.readouterr().out
    assert len([c for c in fake.calls if c[:2] == ["pr", "checks"]]) >= 3


def test_pr_gate_gives_up_after_60s(
    fake: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(time, "monotonic", lambda: float(next(clock)))
    fake.checks_seq = ["[]"]
    assert cli.main(["pr-gate", "12"]) == 1
    assert "no checks reported on PR #12" in capsys.readouterr().out
    polls = len([c for c in fake.calls if c[:2] == ["pr", "checks"]])
    assert 10 <= polls <= 14  # about 60 s at a 5 s interval


def test_pr_gate_names_missing_job(fake: FakeGh, capsys: pytest.CaptureFixture[str]) -> None:
    partial = [{"name": n, "bucket": "pass", "workflow": "ci"} for n in REQUIRED_CI_JOBS[:4]]
    fake.checks_seq = [json.dumps(partial)]
    assert cli.main(["pr-gate", "12"]) == 1
    assert "✗ missing ci job: docker" in capsys.readouterr().out


def test_issue_merge_refuses_missing_job(
    fake: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(board, "set_status", lambda n, s: True)
    partial = [{"name": n, "bucket": "pass", "workflow": "ci"} for n in REQUIRED_CI_JOBS[:4]]
    fake.checks_seq = [json.dumps(partial)]
    assert cli.main(["issue-merge", "12"]) == 1
    assert "✗ missing ci job: docker" in capsys.readouterr().out
    assert not [c for c in fake.calls if c[:2] == ["pr", "merge"]]


def test_issue_merge_no_success_line_on_board_warning(
    fake: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(board, "set_status", lambda n, s: False)  # board warned
    assert cli.main(["issue-merge", "12"]) == 0
    out = capsys.readouterr().out
    assert "-> Merged to milestone" not in out
    assert "✓ merged PR #12 into milestone/m0" in out
    assert "board not updated" in out


def test_issue_merge_success_line_when_board_updated(
    fake: FakeGh, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(board, "set_status", lambda n, s: True)
    assert cli.main(["issue-merge", "12"]) == 0
    assert "#2 -> Merged to milestone" in capsys.readouterr().out


def _milestone_repo(root: Path, n: int) -> None:
    (root / "docs/plans").mkdir(parents=True)
    (root / "docs/specs").mkdir(parents=True)
    (root / ".github/PULL_REQUEST_TEMPLATE").mkdir(parents=True)
    (root / ".github/PULL_REQUEST_TEMPLATE/milestone.md").write_text("# M<N>\n")
    (root / f"docs/plans/2026-m{n}-plan.md").write_text("### M3.T1 Local\n")
    (root / "docs/plans/2026-roadmap.md").write_text("")
    (root / f"docs/specs/x-m{n}-y-design.md").write_text("")
    (root / "docs/specs/x-m4-y-design.md").write_text("")


def test_milestone_start_includes_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _milestone_repo(tmp_path, 3)
    seen: dict[str, Any] = {}

    def fake_start(n: int, tasks: list[Any], url_for: Any, **kw: Any) -> list[str]:
        seen.update(kw, tasks=tasks)
        return []

    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(milestone, "remote_branch_exists", lambda b: False)
    monkeypatch.setattr(milestone, "milestone_start", fake_start)
    assert cli.main(["milestone-start", "3", "--dry-run"]) == 0
    files = seen["start_files"]
    assert "docs/specs/x-m3-y-design.md" in files
    assert "docs/specs/x-m4-y-design.md" not in files
    assert {"docs/plans/2026-m3-plan.md", "docs/plans/2026-roadmap.md"} <= set(files)
    assert [t.id for t in seen["tasks"]] == ["M3.T1"]  # branch absent: working tree


def test_milestone_start_reads_plan_from_origin_when_branch_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _milestone_repo(tmp_path, 3)
    seen: dict[str, Any] = {}
    git_calls: list[list[str]] = []

    def fake_git(args: list[str]) -> str:
        git_calls.append(args)
        if args[0] == "ls-tree":
            return "docs/plans/2026-m3-plan.md\n"
        if args[0] == "show":
            return "### M3.T9 From origin\n"
        return ""

    def fake_start(n: int, tasks: list[Any], url_for: Any, **kw: Any) -> list[str]:
        seen.update(tasks=tasks, url=url_for(tasks[0]))
        return []

    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_git", fake_git)
    monkeypatch.setattr(milestone, "remote_branch_exists", lambda b: True)
    monkeypatch.setattr(milestone, "milestone_start", fake_start)
    assert cli.main(["milestone-start", "3", "--dry-run"]) == 0
    assert [t.id for t in seen["tasks"]] == ["M3.T9"]
    assert ["show", "origin/milestone/m3:docs/plans/2026-m3-plan.md"] in git_calls
    assert seen["url"].endswith("/docs/plans/2026-m3-plan.md#m3t9-from-origin")


def test_load_plan_reads_origin_base(monkeypatch: pytest.MonkeyPatch) -> None:
    git_calls: list[list[str]] = []

    def fake_git(args: list[str]) -> str:
        git_calls.append(args)
        if args[0] == "ls-tree":
            return "docs/plans/2026-m5-x.md\n"
        if args[0] == "show":
            return "### M5.T1 Base\n"
        return ""

    monkeypatch.setattr(cli, "_git", fake_git)
    assert [t.id for t in cli.load_plan(5)] == ["M5.T1"]
    assert ["fetch", "origin", "milestone/m5"] in git_calls
    assert ["show", "origin/milestone/m5:docs/plans/2026-m5-x.md"] in git_calls
    git_calls.clear()
    cli.load_plan(5, "milestone/m5-alt")
    assert ["show", "origin/milestone/m5-alt:docs/plans/2026-m5-x.md"] in git_calls


def test_deviation_notes_drop_their_own_heading() -> None:
    assert cli.deviation_notes("## Deviations from plan\n\n- a\n- b\n") == "- a\n- b"
    assert cli.deviation_notes("# Deviations\n- a") == "- a"
    assert cli.deviation_notes("- a\n\n## Additions\n- b") == "- a\n\n## Additions\n- b"
    assert cli.deviation_notes("") == "None."


def test_issue_pr_puts_notes_under_template_heading(
    fake: FakeGh, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    (root / "docs/plans").mkdir(parents=True)
    (root / ".github/PULL_REQUEST_TEMPLATE").mkdir(parents=True)
    (root / "docs/plans/2026-m0-x.md").write_text("### M0.T2 thing\n")
    template = Path(__file__).parents[3] / ".github/PULL_REQUEST_TEMPLATE/issue.md"
    (root / ".github/PULL_REQUEST_TEMPLATE/issue.md").write_text(template.read_text())
    notes = tmp_path / "notes.md"
    notes.write_text("## Deviations from plan\n\n- Added X.\n")
    monkeypatch.setattr(cli, "repo_root", lambda: root)
    monkeypatch.setattr(cli, "_sh", lambda args, check=True: "2-thing")
    monkeypatch.setattr(board, "set_status", lambda n, s: True)
    assert cli.main(["issue-pr", "2", "fix: t", "--notes", str(notes)]) == 0
    (idx,) = [i for i, c in enumerate(fake.calls) if c[:2] == ["pr", "create"]]
    body = fake.inputs[idx] or ""
    assert body.count("Deviations from plan") == 1
    assert "## Deviations from plan\n- Added X.\n" in body
