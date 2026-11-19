"""Milestone start (issues, branch, draft PR) and the milestone PR roll-up."""

import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from flow import board, gh
from flow.config import APPROVED_LABEL, AUTHOR, CHECKPOINT_LABEL, OWNER, REPO
from flow.plan import Task, task_id_from_title

READY = "Ready"
ISSUE_LIMIT = 200
CHECKPOINT_NOTE = (
    f"**Checkpoint:** requires author approval (`{APPROVED_LABEL}` label) before dependents start."
)
PRS_HEADING = "Issue PRs in this milestone"
CLOSES_HEADING = "Closes"


class MilestoneError(Exception):
    """A condition the user must resolve; reported as a refusal."""


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


_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


def _unfenced(lines: list[str]) -> list[bool]:
    live: list[bool] = []
    fence: str | None = None
    for line in lines:
        m = _FENCE.match(line)
        if fence is None:
            live.append(m is None)
            if m:
                fence = m.group(1)
        else:
            live.append(False)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
    return live


def replace_section(body: str, heading: str, content: str) -> str:
    """Replace the text under `## <heading>` up to the next `## `; the rest is untouched.

    CRLF is normalised to LF. Lines inside code fences never start or end a section.
    """
    lines = body.replace("\r\n", "\n").splitlines(keepends=True)
    live = _unfenced(lines)
    want = f"## {heading}"
    start = next((i for i, x in enumerate(lines) if live[i] and x.rstrip() == want), None)
    if start is None:
        raise ValueError(f"no '## {heading}' section")
    end = next(
        (i for i in range(start + 1, len(lines)) if live[i] and lines[i].startswith("## ")), None
    )
    tail = "\n" if end is not None else ""
    rest = "".join(lines[end:]) if end is not None else ""
    return "".join(lines[:start]) + f"{want}\n" + content.strip("\n") + "\n" + tail + rest


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


def git(args: list[str], env: Mapping[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        env={**os.environ, **env} if env else None,
    )
    if proc.returncode != 0:
        raise gh.GhError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def create_remote_branch(branch: str, message: str, files: Mapping[str, Path]) -> None:
    """Push one commit on origin/main to `branch`, without touching the checkout.

    The commit's tree is origin/main's tree plus `files` (repo path -> local file), so the
    milestone plan is on the branch and the draft PR has a real diff. Built with plumbing
    and a temporary index, so the user's index and working tree are left alone.
    """
    git(["fetch", "origin", "main"])
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git(["read-tree", "origin/main^{tree}"], env)
        for rel, local in files.items():
            blob = git(["hash-object", "-w", str(local)])
            git(["update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}"], env)
        tree = git(["write-tree"], env)
    sha = git(["commit-tree", tree, "-p", "origin/main", "-m", message])
    git(["push", "origin", f"{sha}:refs/heads/{branch}"])


def commits_ahead_of_main(branch: str) -> int:
    data = gh.json(["api", f"repos/{OWNER}/{REPO}/compare/main...{branch}"])
    return int(data["ahead_by"])


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
    start_files: Mapping[str, Path],
) -> list[str]:
    ms_title = milestone_title(n)
    ms_name = re.sub(rf"^M{n}\s*[-—:]?\s*", "", ms_title)
    listed = cast(
        list[dict[str, Any]],
        gh.json(
            ["issue", "list", "--state", "all", "--limit", str(ISSUE_LIMIT)]
            + ["--json", "number,title,body,labels"]
        ),
    )
    if len(listed) >= ISSUE_LIMIT:
        print(
            f"warning: issue list hit its limit of {ISSUE_LIMIT}; some may be missed",
            file=sys.stderr,
        )
    existing: dict[str | None, dict[str, Any]] = {}
    for i in listed:
        tid = task_id_from_title(str(i["title"]))
        if tid in existing:
            print(
                f"warning: {tid} matches issues #{existing[tid]['number']} and #{i['number']};"
                " using the first",
                file=sys.stderr,
            )
            continue
        existing[tid] = i
    statuses = board.try_statuses()
    if statuses is None:
        print("warning: board unreadable; not checking board membership", file=sys.stderr)
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
        if statuses is not None and int(issue["number"]) not in statuses:
            lines.append(f"board {task.id}")  # off the board or no status: fill, never overwrite
            if not dry_run:
                board.set_status(int(issue["number"]), READY)
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
    branch_existed = remote_branch_exists(branch)
    if branch_existed:
        lines.append(f"branch exists {branch}")
    else:
        lines.append(f"branch create {branch} from origin/main (+ start commit with the plan)")
        if not dry_run:
            create_remote_branch(branch, f"chore: start milestone m{n}", start_files)
    pr = _open_pr_number(branch)
    if pr is not None:
        lines.append(f"pr exists #{pr}")
        return lines
    if branch_existed and commits_ahead_of_main(branch) == 0:
        raise MilestoneError(
            f"{branch} has no commits ahead of main, so GitHub can't open a PR for it; "
            f"push a commit to it (or delete the branch) and rerun `flow milestone-start {n}`"
        )
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
        raise MilestoneError(f"no open milestone PR for {branch}; run `flow milestone-start {n}`")
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
    if normalize(new) == normalize(str(pr["body"])):
        return [f"unchanged PR #{pr['number']}"]
    gh.run(["pr", "edit", str(pr["number"]), "--body-file", "-"], input=new)
    return [f"updated PR #{pr['number']}"]
