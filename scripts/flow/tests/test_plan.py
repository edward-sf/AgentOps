from pathlib import Path

import pytest

from flow.plan import find_plan, parse_plan, task_id_from_title

SAMPLE = (Path(__file__).parent / "fixtures" / "plan_sample.md").read_text()


def test_parse_plan_extracts_tasks_in_order() -> None:
    tasks = parse_plan(SAMPLE)
    assert [t.id for t in tasks] == ["M9.T1", "M9.T2", "M9.T3"]
    assert tasks[0].title == "First task"
    assert (tasks[0].milestone, tasks[0].number) == (9, 1)


def test_parse_plan_reads_acceptance_checkpoint_and_depends() -> None:
    t1, t2, t3 = parse_plan(SAMPLE)
    assert t1.acceptance == ["Criterion one", "Criterion two"]
    assert t3.checkpoint is True and t1.checkpoint is False
    assert t1.depends_on == []
    assert t2.depends_on == ["M9.T1"]
    assert t3.depends_on == ["M9.T1", "M9.T2"]


def test_parse_plan_ignores_non_task_headings() -> None:
    tasks = parse_plan(SAMPLE + "\n### Notes\ntext\n")
    assert all(t.id.startswith("M9.T") for t in tasks)
    assert "text" not in tasks[-1].body


def test_inline_marker_prose_is_not_a_checkpoint() -> None:
    text = (
        "### M1.T1 Prose\n\n"
        "**Depends on:** none\n"
        "- An optional `**Checkpoint**` line.\n"
        "Mentions `**Depends on:** M1.T9` mid-sentence and `**Acceptance criteria**` too.\n"
        "\n**Acceptance criteria**\n- [ ] Real one\n"
    )
    (task,) = parse_plan(text)
    assert task.checkpoint is False
    assert task.depends_on == []
    assert task.acceptance == ["Real one"]


def test_markers_inside_code_fences_and_separators_in_fences() -> None:
    text = "### M1.T1 Fenced\n\n```md\n---\n### M1.T2 Fake\n**Checkpoint**\n```\n\nafter\n"
    (task,) = parse_plan(text)
    assert task.checkpoint is False
    assert "after" in task.body


def test_acceptance_stops_at_non_bullet_and_joins_subbullets() -> None:
    text = (
        "### M1.T1 A\n\n**Acceptance criteria**\n- [ ] One:\n  - sub a\n- [ ] Two\n\n"
        "**Files:**\n- Create: x\n- [ ] **Step 1**\n"
    )
    (task,) = parse_plan(text)
    assert task.acceptance == ["One: sub a", "Two"]


def test_task_id_from_title() -> None:
    assert task_id_from_title("M0.T3 Data model and first migration") == "M0.T3"
    assert task_id_from_title("fix typo") is None


def test_find_plan_errors_on_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "docs/plans").mkdir(parents=True)
    for n in ("a-m1-x.md", "b-m1-y.md"):
        (tmp_path / "docs/plans" / n).write_text("")
    with pytest.raises(FileNotFoundError):
        find_plan(1, tmp_path)


def test_find_plan_errors_on_none_and_returns_single(tmp_path: Path) -> None:
    (tmp_path / "docs/plans").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        find_plan(1, tmp_path)
    (tmp_path / "docs/plans/2026-m1-x.md").write_text("")
    assert find_plan(1, tmp_path).name == "2026-m1-x.md"


def test_indented_checkpoint_line_does_not_count() -> None:
    (task,) = parse_plan("### M1.T1 A\n\n  **Checkpoint**\n")
    assert task.checkpoint is False
    (task,) = parse_plan("### M1.T1 A\n\n**Checkpoint**  \n")
    assert task.checkpoint is True


def test_find_plan_ignores_the_milestone_plan_template(tmp_path: Path) -> None:
    (tmp_path / "docs/plans").mkdir(parents=True)
    (tmp_path / "docs/plans/TEMPLATE-milestone-plan.md").write_text("")
    (tmp_path / "docs/plans/2026-m1-x.md").write_text("")
    assert find_plan(1, tmp_path).name == "2026-m1-x.md"
    template = Path(__file__).parents[3] / "docs/plans/TEMPLATE-milestone-plan.md"
    assert template.exists() and parse_plan(template.read_text()) == []
