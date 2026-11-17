from typing import Any

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
