"""Enforcement glue (issue-merge, post-verdict, checks) with a fake gh layer."""

import json
from pathlib import Path
from typing import Any

import pytest

from flow import board, cli, gh
from flow.config import REQUIRED_CI_JOBS
from flow.verdict import format_verdict

SHA = "a" * 40
OTHER = "b" * 40


class FakeGh:
    def __init__(self, **pr_overrides: Any) -> None:
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
        self.pr.update(pr_overrides)
        self.checks_out = json.dumps(
            [{"name": n, "bucket": "pass", "link": "", "workflow": "ci"} for n in REQUIRED_CI_JOBS]
        )
        self.merge_error: gh.GhError | None = None
        self.state_after_merge = "MERGED"
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []

    def run(
        self, args: list[str], *, input: str | None = None, allow: tuple[int, ...] = (0,)
    ) -> str:
        self.calls.append(args)
        self.inputs.append(input)
        if args[:2] == ["pr", "checks"]:
            return self.checks_out
        if args[:2] == ["pr", "merge"] and self.merge_error:
            self.pr["state"] = self.state_after_merge
            raise self.merge_error
        return ""

    def json(self, args: list[str], *, allow: tuple[int, ...] = (0,)) -> Any:
        if args[:2] == ["pr", "view"]:
            return dict(self.pr)
        if args[:2] == ["issue", "view"]:
            return {"number": 2, "title": "M0.T2 thing", "labels": [], "url": "", "state": "OPEN"}
        raise AssertionError(args)

    def named(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    f = FakeGh()
    monkeypatch.setattr(gh, "run", f.run)
    monkeypatch.setattr(gh, "json", f.json)
    monkeypatch.setattr(cli, "load_plan", lambda m, base=None: [])

    def set_status(n: int, s: str) -> bool:
        f.calls.append(["board", str(n), s])
        return True

    monkeypatch.setattr(board, "set_status", set_status)
    return f


def merge(pr: int = 12) -> int:
    return cli.main(["issue-merge", str(pr)])


def test_merge_refuses_pending_ci(fake: FakeGh, capsys: pytest.CaptureFixture[str]) -> None:
    fake.checks_out = json.dumps([{"name": "ci", "bucket": "pending", "link": ""}])
    assert merge() == 1
    assert "✗ ci is pending" in capsys.readouterr().out
    assert fake.named("pr", "merge") == []


def test_merge_refuses_without_pass_at_head(
    fake: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.pr["comments"] = [
        {"author": {"login": "edward-sf"}, "body": format_verdict("PASS", OTHER, "")}
    ]
    assert merge() == 1
    assert "no PASS verdict at head" in capsys.readouterr().out
    assert fake.named("pr", "merge") == []


def test_merge_ignores_verdict_from_other_author(fake: FakeGh) -> None:
    fake.pr["comments"] = [
        {"author": {"login": "mallory"}, "body": format_verdict("PASS", SHA, "")}
    ]
    assert merge() == 1
    assert fake.named("pr", "merge") == []


def test_merge_refuses_branch_not_matching_issue(
    fake: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.pr["headRefName"] = "7-other"
    assert merge() == 1
    assert "✗ branch does not match issue #2" in capsys.readouterr().out
    assert fake.named("pr", "merge") == []


def test_merge_success_argv(fake: FakeGh) -> None:
    assert merge() == 0
    (argv,) = fake.named("pr", "merge")
    assert argv[:3] == ["pr", "merge", "12"]
    assert "--squash" in argv and "--delete-branch" in argv
    assert argv[argv.index("--subject") + 1] == "feat(x): thing"
    assert argv[argv.index("--match-head-commit") + 1] == SHA
    assert ["board", "2", "Merged to milestone"] in fake.calls


def test_merge_error_but_pr_merged_warns_and_continues(
    fake: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.merge_error = gh.GhError("failed to delete local branch")
    assert merge() == 0
    assert "warning" in capsys.readouterr().err
    assert ["board", "2", "Merged to milestone"] in fake.calls


def test_merge_error_and_pr_still_open_fails(fake: FakeGh) -> None:
    fake.merge_error = gh.GhError("head changed")
    fake.state_after_merge = "OPEN"
    assert merge() == 1
    assert ["board", "2", "Merged to milestone"] not in fake.calls


@pytest.mark.parametrize(
    "out, expected",
    [
        ('[{"name":"a","bucket":"pass","link":""}]', ["pass"]),
        ('[{"name":"a","bucket":"fail","link":""}]', ["fail"]),
        ('[{"name":"a","bucket":"pending","link":""}]', ["pending"]),
        ("no checks reported on the 'x' branch", []),
        ("", []),
    ],
)
def test_checks_maps_output(fake: FakeGh, out: str, expected: list[str]) -> None:
    fake.checks_out = out
    assert [c["bucket"] for c in cli.checks(12)] == expected
    assert (
        cli.ci_from_buckets(expected)
        == {
            "pass": "success",
            "fail": "failure",
            "pending": "pending",
            None: "missing",
        }[expected[0] if expected else None]
    )


def test_checks_tolerates_gh_exit_codes_1_and_8(
    fake: FakeGh, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli.checks(12)
    assert fake.named("pr", "checks")
    # the run wrapper is told that exit 1 (failed) and 8 (pending) still carry JSON
    seen: dict[str, Any] = {}

    def spy(args: list[str], *, input: str | None = None, allow: tuple[int, ...] = (0,)) -> str:
        seen["allow"] = allow
        return "[]"

    monkeypatch.setattr(gh, "run", spy)
    cli.checks(12)
    assert set(seen["allow"]) >= {0, 1, 8}


def test_post_verdict_refuses_when_head_moved(
    fake: FakeGh, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "f.md"
    f.write_text("- ok")
    assert cli.main(["post-verdict", "12", "PASS", str(f), "--sha", OTHER]) == 1
    assert f"✗ head moved: reviewed {OTHER[:7]}, current {SHA[:7]}" in capsys.readouterr().out
    assert fake.named("pr", "comment") == []


def test_post_verdict_posts_given_sha(fake: FakeGh, tmp_path: Path) -> None:
    f = tmp_path / "f.md"
    f.write_text("- ok")
    assert cli.main(["post-verdict", "12", "pass", str(f), "--sha", SHA]) == 0
    idx = fake.calls.index(fake.named("pr", "comment")[0])
    assert (fake.inputs[idx] or "").splitlines()[0] == f"Review verdict: PASS @ {SHA}"


def test_post_verdict_requires_full_sha(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(["post-verdict", "12", "PASS", "f", "--sha", "abc123"])
    with pytest.raises(SystemExit):
        cli.main(["post-verdict", "12", "PASS", "f"])
