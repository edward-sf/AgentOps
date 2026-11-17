"""Milestone start (issues, branch, draft PR) and the milestone PR roll-up."""

import re
import subprocess
from collections.abc import Callable
from typing import Any, cast

from flow import board, gh
from flow.config import APPROVED_LABEL, AUTHOR, CHECKPOINT_LABEL, OWNER, REPO
from flow.plan import Task, task_id_from_title

READY = "Ready"
CHECKPOINT_NOTE = (
    f"**Checkpoint:** requires author approval (`{APPROVED_LABEL}` label) before dependents start."
)
PRS_HEADING = "Issue PRs in this milestone"
CLOSES_HEADING = "Closes"


# ---------- pure rendering ----------


def anchor(heading: str) -> str:
    """GitHub's heading slug: lowercase, drop characters outside [a-z0-9 -], spaces to hyphens."""
    return re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")


def goal_of(task: Task) -> str:
    """The first paragraph after `**Goal:**` in the task body."""
    lines = task.body.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("**Goal:**"):
            para = [line[len("**Goal:**") :].strip()]
            for nxt in lines[i + 1 :]:
                if not nxt.strip():
                    break
                para.append(nxt.rstrip())
            return "\n".join(p for p in para if p)
    return ""


def depends_text(task: Task) -> str:
    for line in task.body.splitlines():
        if line.startswith("**Depends on:**"):
            return line[len("**Depends on:**") :].strip()
    return "none"


def criteria_of(task: Task) -> str:
    """The acceptance block verbatim, nested bullets included."""
    lines = task.body.splitlines()
    start = next((i for i, x in enumerate(lines) if x.startswith("**Acceptance criteria**")), None)
    if start is None:
        return "\n".join(f"- [ ] {a}" for a in task.acceptance)
    block: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith(("- [", " ", "\t")):
            break
        block.append(line.rstrip())
    return "\n".join(block).strip("\n")


def render_issue_body(task: Task, plan_url: str) -> str:
    parts = [
        f"### Plan section\n{plan_url}\n",
        f"### Goal\n{goal_of(task)}\n",
        f"**Depends on:** {depends_text(task)}\n",
    ]
    if task.checkpoint:
        parts.append(f"{CHECKPOINT_NOTE}\n")
    parts.append(f"### Acceptance criteria\n{criteria_of(task)}\n")
    return "\n".join(parts)


def normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip()


def replace_section(body: str, heading: str, content: str) -> str:
    """Replace the text under `## <heading>` up to the next `## `; the rest is untouched."""
    start = re.search(rf"^## {re.escape(heading)}[ \t]*\n", body, re.MULTILINE)
    if start is None:
        raise ValueError(f"no '## {heading}' section")
    nxt = re.search(r"^## ", body[start.end() :], re.MULTILINE)
    end = start.end() + nxt.start() if nxt else len(body)
    tail = "\n" if nxt else ""
    return body[: start.end()] + content.strip("\n") + "\n" + tail + body[end:]


def render_rollup(prs: list[tuple[int, str]], issues: list[int]) -> tuple[str, str]:
    prs_text = "\n".join(f"- #{n} — {title}" for n, title in prs)
    closes_text = "\n".join(f"Closes #{n}" for n in issues)
    return prs_text, closes_text


# ---------- environment ----------


def remote_branch_exists(branch: str) -> bool:
    proc = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", branch], capture_output=True
    )
    if proc.returncode == 2:  # --exit-code: no matching refs
        return False
    if proc.returncode != 0:
        raise gh.GhError(f"git ls-remote failed: {proc.stderr.decode().strip()}")
    return True


def create_remote_branch(branch: str) -> None:
    for cmd in (
        ["git", "fetch", "origin", "main"],
        ["git", "push", "origin", f"origin/main:refs/heads/{branch}"],
    ):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise gh.GhError(f"{' '.join(cmd[:2])} failed: {proc.stderr.strip()}")


def milestone_title(n: int) -> str:
    found = gh.json(["api", f"repos/{OWNER}/{REPO}/milestones?state=all&per_page=100"])
    for m in cast(list[dict[str, Any]], found):
        if re.match(rf"M{n}\b", str(m["title"])):
            return str(m["title"])
    raise gh.GhError(f"no GitHub milestone titled 'M{n} ...'")


def _open_pr_number(head: str) -> int | None:
    prs = gh.json(["pr", "list", "--state", "open", "--head", head, "--json", "number"])
    return int(prs[0]["number"]) if prs else None


def _labels(issue: dict[str, Any]) -> set[str]:
    return {str(label["name"]) for label in issue.get("labels", [])}


# ---------- milestone-start ----------


def milestone_start(
    n: int,
    tasks: list[Task],
    url_for: Callable[[Task], str],
    *,
    dry_run: bool,
    pr_template: str,
) -> list[str]:
    ms_title = milestone_title(n)
    ms_name = re.sub(rf"^M{n}\s*[-—:]?\s*", "", ms_title)
    existing = {
        task_id_from_title(str(i["title"])): i
        for i in cast(
            list[dict[str, Any]],
            gh.json(
                ["issue", "list", "--state", "all", "--limit", "200"]
                + ["--json", "number,title,body,labels"]
            ),
        )
    }
    lines: list[str] = []
    for task in tasks:
        title = f"{task.id} {task.title}"
        body = render_issue_body(task, url_for(task))
        issue = existing.get(task.id)
        if issue is None:
            lines.append(f"create {task.id}")
            if not dry_run:
                _create_issue(title, body, ms_title, task.checkpoint)
            continue
        has_label = CHECKPOINT_LABEL in _labels(issue)
        drift = (
            issue["title"] != title
            or normalize(str(issue["body"] or "")) != normalize(body)
            or has_label != task.checkpoint
        )
        if not drift:
            lines.append(f"unchanged {task.id}")
            continue
        lines.append(f"update {task.id}")
        if not dry_run:
            _update_issue(int(issue["number"]), title, body, has_label, task.checkpoint)

    branch = f"milestone/m{n}"
    if remote_branch_exists(branch):
        lines.append(f"branch exists {branch}")
    else:
        lines.append(f"branch create {branch} from origin/main")
        if not dry_run:
            create_remote_branch(branch)
    pr = _open_pr_number(branch)
    if pr is not None:
        lines.append(f"pr exists #{pr}")
    else:
        lines.append(f"pr create (draft) {branch} -> main")
        if not dry_run:
            pr_body = pr_template.replace("<N>", str(n)).replace("<milestone name>", ms_name)
            gh.run(
                ["pr", "create", "--draft", "--base", "main", "--head", branch]
                + ["--title", f"M{n} — {ms_name}", "--body-file", "-"],
                input=pr_body,
            )
    return lines


def _create_issue(title: str, body: str, ms_title: str, checkpoint: bool) -> None:
    args = ["issue", "create", "--title", title, "--body-file", "-"]
    args += ["--milestone", ms_title, "--assignee", AUTHOR]
    if checkpoint:
        args += ["--label", CHECKPOINT_LABEL]
    out = gh.run(args, input=body)
    m = re.search(r"/issues/(\d+)", out)
    if m is None:
        raise gh.GhError(f"could not read the new issue number from: {out.strip()}")
    board.set_status(int(m.group(1)), READY)


def _update_issue(number: int, title: str, body: str, has_label: bool, checkpoint: bool) -> None:
    """Title, body and the checkpoint label only. Never the board status or other labels."""
    args = ["issue", "edit", str(number), "--title", title, "--body-file", "-"]
    if checkpoint and not has_label:
        args += ["--add-label", CHECKPOINT_LABEL]
    elif has_label and not checkpoint:
        args += ["--remove-label", CHECKPOINT_LABEL]
    gh.run(args, input=body)


# ---------- milestone-pr ----------


def _part_of(body: str) -> int | None:
    m = re.search(r"Part of #(\d+)", body or "")
    return int(m.group(1)) if m else None


def milestone_pr(n: int, *, dry_run: bool) -> list[str]:
    branch = f"milestone/m{n}"
    prs = gh.json(["pr", "list", "--state", "open", "--head", branch, "--json", "number,body"])
    if not prs:
        raise gh.GhError(f"no open milestone PR for {branch}; run `flow milestone-start {n}`")
    pr = prs[0]
    merged = cast(
        list[dict[str, Any]],
        gh.json(
            ["pr", "list", "--state", "merged", "--base", branch, "--limit", "200"]
            + ["--json", "number,title,body,mergedAt"]
        ),
    )
    issue_prs = sorted(
        (p for p in merged if _part_of(p["body"]) is not None), key=lambda p: str(p["mergedAt"])
    )
    pr_lines = [(int(p["number"]), str(p["title"])) for p in issue_prs]
    issues: list[int] = []
    for p in issue_prs:
        num = _part_of(p["body"])
        if num is not None and num not in issues:
            issues.append(num)
    prs_text, closes_text = render_rollup(pr_lines, issues)
    new = replace_section(str(pr["body"]), PRS_HEADING, prs_text or "_None yet._")
    new = replace_section(new, CLOSES_HEADING, closes_text)
    if dry_run:
        return [new.rstrip("\n")]
    if new == pr["body"]:
        return [f"unchanged PR #{pr['number']}"]
    gh.run(["pr", "edit", str(pr["number"]), "--body-file", "-"], input=new)
    return [f"updated PR #{pr['number']}"]
