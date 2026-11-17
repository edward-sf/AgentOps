"""flow: scripted, deterministic steps of the agent workflow, over the gh CLI."""

import argparse
import json as _json
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, cast

from flow import board, gh, milestone
from flow.config import APPROVED_LABEL, AUTHOR, CHECKPOINT_LABEL, OWNER, REPO
from flow.guard import MERGED_STATUSES, DepState, PrState, merge_blockers, start_blockers
from flow.plan import Task, find_plan, parse_plan, task_id_from_title
from flow.verdict import Result, format_verdict, latest_verdict_for

Ci = Literal["success", "failure", "pending", "missing"]
MERGED = "Merged to milestone"
PR_FIELDS = "number,title,body,state,headRefName,headRefOid,baseRefName,comments"


class Refusal(Exception):
    def __init__(self, reasons: Sequence[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


# ---------- pure helpers ----------


def part_of(body: str) -> int | None:
    m = re.search(r"Part of #(\d+)", body or "")
    return int(m.group(1)) if m else None


def milestone_of_title(title: str) -> int | None:
    task_id = task_id_from_title(title)
    return int(task_id[1 : task_id.index(".")]) if task_id else None


def slug_for(title: str, words: int = 5) -> str:
    rest = re.sub(r"^M\d+\.T\d+\s*", "", title.strip())
    parts = re.sub(r"[^a-z0-9]+", " ", rest.lower()).split()
    return "-".join(parts[:words])


def heading_anchor(heading: str) -> str:
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def ci_from_buckets(buckets: list[str]) -> Ci:
    """success only if every check passed or was skipped."""
    if not buckets:
        return "missing"
    if any(b in ("fail", "cancel") for b in buckets):
        return "failure"
    if all(b in ("pass", "skipping") for b in buckets):
        return "success"
    return "pending"


def rollup_bucket(check: dict[str, Any]) -> str:
    if "state" in check and "conclusion" not in check:  # StatusContext
        return {"SUCCESS": "pass", "PENDING": "pending", "EXPECTED": "pending"}.get(
            str(check["state"]), "fail"
        )
    if check.get("status") != "COMPLETED":
        return "pending"
    conclusion = str(check.get("conclusion"))
    if conclusion == "SUCCESS":
        return "pass"
    if conclusion in ("SKIPPED", "NEUTRAL"):
        return "skipping"
    return "cancel" if conclusion == "CANCELLED" else "fail"


def author_comments(comments: list[dict[str, Any]]) -> list[str]:
    """Bodies of the author's comments, oldest first. Verdicts from anyone else don't count."""
    return [
        str(c.get("body", "")) for c in comments if (c.get("author") or {}).get("login") == AUTHOR
    ]


def effective_status(board_status: str | None, has_merged_pr: bool) -> str | None:
    if has_merged_pr and (board_status or "").lower() not in MERGED_STATUSES:
        return MERGED
    return board_status


# ---------- environment helpers ----------


def _sh(args: list[str], *, check: bool = True) -> str:
    proc = subprocess.run(args, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise gh.GhError(f"{' '.join(args[:3])} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def repo_root() -> Path:
    return Path(_sh(["git", "rev-parse", "--show-toplevel"]))


def load_plan(milestone: int) -> list[Task]:
    return parse_plan(find_plan(milestone, repo_root()).read_text())


def task_for(task_id: str) -> Task | None:
    m = int(task_id[1 : task_id.index(".")])
    return next((t for t in load_plan(m) if t.id == task_id), None)


def get_issue(number: int) -> dict[str, Any]:
    data = gh.json(["issue", "view", str(number), "--json", "number,title,labels,url,state"])
    return cast(dict[str, Any], data)


def label_names(issue: dict[str, Any]) -> set[str]:
    return {str(label["name"]) for label in issue.get("labels", [])}


def issue_context(number: int) -> tuple[dict[str, Any], str, int]:
    issue = get_issue(number)
    task_id = task_id_from_title(issue["title"])
    if task_id is None:
        raise Refusal([f"issue #{number} title has no plan task ID (M<N>.T<n>)"])
    return issue, task_id, int(task_id[1 : task_id.index(".")])


def pr_view(pr: int) -> dict[str, Any]:
    return cast(dict[str, Any], gh.json(["pr", "view", str(pr), "--json", PR_FIELDS]))


def checks(pr: int) -> list[dict[str, Any]]:
    """Check runs on the PR head. `gh pr checks` exits 1 (failed) or 8 (pending) with JSON."""
    out = gh.run(["pr", "checks", str(pr), "--json", "name,bucket,link"], allow=(0, 1, 8))
    if not out.strip().startswith("["):  # e.g. "no checks reported"
        return []
    return cast(list[dict[str, Any]], _json.loads(out))


def merged_prs(milestone: int) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        gh.json(
            [
                "pr",
                "list",
                "--state",
                "merged",
                "--base",
                f"milestone/m{milestone}",
                "--limit",
                "200",
                "--json",
                "number,body",
            ]
        ),
    )


def all_issues() -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        gh.json(
            ["issue", "list", "--state", "all", "--limit", "200", "--json", "number,title,labels"]
        ),
    )


# ---------- commands ----------


def cmd_status(args: argparse.Namespace) -> int:
    milestone = args.milestone
    if milestone is None:
        m = re.match(r"milestone/m(\d+)$", _sh(["git", "branch", "--show-current"]))
        if not m:
            raise Refusal(["pass a milestone number, e.g. `flow status 0`"])
        milestone = int(m.group(1))
    issues = [i for i in all_issues() if milestone_of_title(i["title"]) == milestone]
    issues.sort(key=lambda i: int(str(task_id_from_title(i["title"])).split(".T")[1]))
    prs = cast(
        list[dict[str, Any]],
        gh.json(
            [
                "pr",
                "list",
                "--state",
                "all",
                "--base",
                f"milestone/m{milestone}",
                "--limit",
                "200",
                "--json",
                "number,state,body,headRefName,headRefOid,comments,statusCheckRollup",
            ]
        ),
    )
    by_issue: dict[int, list[dict[str, Any]]] = {}
    for pr in prs:
        n = part_of(pr["body"])
        if n is not None:
            by_issue.setdefault(n, []).append(pr)
    statuses = board.statuses()
    for issue in issues:
        n = issue["number"]
        its = sorted(by_issue.get(n, []), key=lambda p: int(p["number"]))
        has_merged = any(p["state"] == "MERGED" for p in its)
        status = effective_status(statuses.get(n), has_merged) or "-"
        latest = its[-1] if its else None
        if latest is None:
            branch, pr_col, ci, verdict = "-", "-", "-", "-"
        else:
            branch, pr_col = latest["headRefName"], str(latest["number"])
            ci = ci_from_buckets([rollup_bucket(c) for c in latest["statusCheckRollup"] or []])
            v = latest_verdict_for(author_comments(latest["comments"]), latest["headRefOid"])
            verdict = v.result if v else "-"
        tid = task_id_from_title(issue["title"])
        print(f"#{n}  {tid}  {status}  {branch}  PR#{pr_col}  ci={ci}  verdict={verdict}")
    return 0


def cmd_issue_start(args: argparse.Namespace) -> int:
    issue, task_id, milestone = issue_context(args.issue)
    task = next((t for t in load_plan(milestone) if t.id == task_id), None)
    if task is None:
        raise Refusal([f"{task_id} not found in the M{milestone} plan"])
    issues = all_issues()
    statuses = board.statuses()
    merged_cache: dict[int, list[dict[str, Any]]] = {}
    deps: list[DepState] = []
    for dep_id in task.depends_on:
        dep_issue = next((i for i in issues if task_id_from_title(i["title"]) == dep_id), None)
        if dep_issue is None:
            raise Refusal([f"no issue found for dependency {dep_id}"])
        dep_m = int(dep_id[1 : dep_id.index(".")])
        if dep_m not in merged_cache:
            merged_cache[dep_m] = merged_prs(dep_m)
        has_merged = any(part_of(p["body"]) == dep_issue["number"] for p in merged_cache[dep_m])
        labels = label_names(dep_issue)
        dep_task = task_for(dep_id)
        deps.append(
            DepState(
                dep_id,
                effective_status(statuses.get(dep_issue["number"]), has_merged),
                CHECKPOINT_LABEL in labels or bool(dep_task and dep_task.checkpoint),
                APPROVED_LABEL in labels,
            )
        )
    blockers = start_blockers(deps)
    if blockers:
        raise Refusal(blockers)
    branch = f"{args.issue}-{slug_for(issue['title'])}"
    _sh(["git", "fetch", "origin"])
    _sh(["git", "switch", "-c", branch, f"origin/milestone/m{milestone}"])
    board.set_status(args.issue, "In progress")
    print(f"✓ started #{args.issue} {task_id} on {branch}")
    return 0


def cmd_issue_pr(args: argparse.Namespace) -> int:
    issue, task_id, milestone = issue_context(args.issue)
    branch = _sh(["git", "branch", "--show-current"])
    if not branch.startswith(f"{args.issue}-"):
        raise Refusal([f"current branch '{branch}' is not an issue-{args.issue} branch"])
    root = repo_root()
    plan_path = find_plan(milestone, root)
    task = next((t for t in parse_plan(plan_path.read_text()) if t.id == task_id), None)
    rel = plan_path.relative_to(root).as_posix()
    link = f"https://github.com/{OWNER}/{REPO}/blob/milestone/m{milestone}/{rel}"
    if task:
        link += "#" + heading_anchor(f"{task.id} {task.title}")
    summary = args.summary or "<!-- What this PR does, in a few sentences. -->"
    notes = Path(args.notes).read_text().strip() if args.notes else "None."
    template = (root / ".github/PULL_REQUEST_TEMPLATE/issue.md").read_text()
    body = (
        template.replace("{{issue}}", str(args.issue))
        .replace("{{plan_link}}", link)
        .replace("{{notes}}", notes)
        .replace("<!-- What this PR does, in a few sentences. -->", summary)
    )
    _sh(["git", "push", "-u", "origin", "HEAD"])
    url = (
        gh.run(
            [
                "pr",
                "create",
                "--base",
                f"milestone/m{milestone}",
                "--head",
                branch,
                "--title",
                args.title,
                "--body-file",
                "-",
            ],
            input=body,
        )
        .strip()
        .splitlines()[-1]
    )
    board.set_status(args.issue, "In review")
    print(f"✓ opened {url}")
    return 0


def cmd_pr_gate(args: argparse.Namespace) -> int:
    pr = args.pr
    for _ in range(18):  # checks can take a moment to register after a push
        if checks(pr):
            break
        time.sleep(5)
    else:
        raise Refusal([f"no checks reported on PR #{pr}"])
    rc = subprocess.run(["gh", "pr", "checks", str(pr), "--watch", "--interval", "20"]).returncode
    final = checks(pr)
    if rc == 0 and ci_from_buckets([c["bucket"] for c in final]) == "success":
        print(f"✓ ci green on PR #{pr}")
        return 0
    failing = [c for c in final if c["bucket"] in ("fail", "cancel")]
    for c in failing:
        print(f"✗ failing check: {c['name']}")
    seen: set[str] = set()
    for c in failing:
        m = re.search(r"/runs/(\d+)", str(c.get("link", "")))
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            try:
                log = gh.run(["run", "view", m.group(1), "--log-failed"])
            except gh.GhError as e:
                log = str(e)
            print("\n".join(log.splitlines()[-60:]))
    if not failing:
        print(f"✗ gate did not finish green (watch exit {rc})")
    return 1


def cmd_review_context(args: argparse.Namespace) -> int:
    pr = pr_view(args.pr)
    n = part_of(pr["body"])
    if n is None:
        raise Refusal([f"PR #{args.pr} body has no 'Part of #<n>'"])
    _, task_id, milestone = issue_context(n)
    task = next((t for t in load_plan(milestone) if t.id == task_id), None)
    if task is None:
        raise Refusal([f"{task_id} not found in the M{milestone} plan"])
    print(f"## Task {task.id}\n{task.body}\n")
    print("## Acceptance criteria")
    for item in task.acceptance:
        print(f"- {item}")
    print(f"\n## Head {pr['headRefOid']}\n\n## Diff")
    print(gh.run(["pr", "diff", str(args.pr)]))
    return 0


def cmd_post_verdict(args: argparse.Namespace) -> int:
    pr = pr_view(args.pr)
    if pr["state"] != "OPEN":
        raise Refusal([f"PR #{args.pr} is {pr['state']}, not open"])
    head = pr["headRefOid"]
    if head != args.sha:
        raise Refusal([f"head moved: reviewed {args.sha[:7]}, current {head[:7]}"])
    findings = Path(args.file).read_text()
    result = cast(Result, args.result)
    body = format_verdict(result, args.sha, findings)
    gh.run(["pr", "comment", str(args.pr), "--body-file", "-"], input=body)
    print(f"✓ {body.splitlines()[0]}")
    return 0


def cmd_issue_merge(args: argparse.Namespace) -> int:
    pr = pr_view(args.pr)
    if pr["state"] != "OPEN":
        raise Refusal([f"PR #{args.pr} is {pr['state']}, not open"])
    n = part_of(pr["body"])
    if n is None:
        raise Refusal([f"PR #{args.pr} body has no 'Part of #<n>'"])
    issue, task_id, milestone = issue_context(n)
    labels = label_names(issue)
    task = next((t for t in load_plan(milestone) if t.id == task_id), None)
    state = PrState(
        number=args.pr,
        head_sha=pr["headRefOid"],
        base=pr["baseRefName"],
        ci=ci_from_buckets([c["bucket"] for c in checks(args.pr)]),
        comments=author_comments(pr["comments"]),
        checkpoint=CHECKPOINT_LABEL in labels or bool(task and task.checkpoint),
        checkpoint_approved=APPROVED_LABEL in labels,
    )
    blockers = merge_blockers(state)
    if not str(pr["headRefName"]).startswith(f"{n}-"):
        blockers.append(f"branch does not match issue #{n}")
    if blockers:
        raise Refusal(blockers)
    try:
        gh.run(
            [
                "pr",
                "merge",
                str(args.pr),
                "--squash",
                "--subject",
                pr["title"],
                "--delete-branch",
                "--match-head-commit",
                state.head_sha,
            ]
        )
    except gh.GhError as e:
        # e.g. --delete-branch failing locally after the remote merge succeeded
        if pr_view(args.pr)["state"] != "MERGED":
            raise
        print(f"warning: gh pr merge reported an error but the PR is merged: {e}", file=sys.stderr)
    board.set_status(n, MERGED)
    print(f"✓ merged PR #{args.pr} into {state.base}; #{n} -> {MERGED}")
    return 0


def cmd_milestone_start(args: argparse.Namespace) -> int:
    root = repo_root()
    plan_path = find_plan(args.milestone, root)
    rel = plan_path.relative_to(root).as_posix()
    base = f"https://github.com/{OWNER}/{REPO}/blob/milestone/m{args.milestone}/{rel}"
    template = (root / ".github/PULL_REQUEST_TEMPLATE/milestone.md").read_text()
    lines = milestone.milestone_start(
        args.milestone,
        parse_plan(plan_path.read_text()),
        lambda t: f"{base}#{milestone.anchor(f'{t.id} {t.title}')}",
        dry_run=args.dry_run,
        pr_template=template,
    )
    print("\n".join(lines))
    return 0


def cmd_milestone_pr(args: argparse.Namespace) -> int:
    try:
        lines = milestone.milestone_pr(args.milestone, dry_run=args.dry_run)
    except ValueError as e:
        raise Refusal([f"milestone PR body: {e}"]) from e
    print("\n".join(lines))
    return 0


# ---------- entry point ----------


def _sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise argparse.ArgumentTypeError("must be a 40-character lowercase hex SHA")
    return value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="flow", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("status", help="one row per milestone issue")
    s.add_argument("milestone", nargs="?", type=int)
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("issue-start", help="check dependencies, branch, mark In progress")
    s.add_argument("issue", type=int)
    s.set_defaults(fn=cmd_issue_start)
    s = sub.add_parser("issue-pr", help="push and open the issue PR")
    s.add_argument("issue", type=int)
    s.add_argument("title")
    s.add_argument("--notes", help="file with deviation notes")
    s.add_argument("--summary", help="text replacing the template's Summary placeholder")
    s.set_defaults(fn=cmd_issue_pr)
    s = sub.add_parser("pr-gate", help="wait for CI; print failing logs")
    s.add_argument("pr", type=int)
    s.set_defaults(fn=cmd_pr_gate)
    s = sub.add_parser("review-context", help="print task, criteria, head SHA and diff")
    s.add_argument("pr", type=int)
    s.set_defaults(fn=cmd_review_context)
    s = sub.add_parser("post-verdict", help="comment a verdict at the current head")
    s.add_argument("pr", type=int)
    s.add_argument("result", type=str.upper, choices=["PASS", "FAIL"])
    s.add_argument("file", help="file with findings")
    s.add_argument(
        "--sha", required=True, type=_sha, help="reviewed head SHA (from review-context)"
    )
    s.set_defaults(fn=cmd_post_verdict)
    s = sub.add_parser("issue-merge", help="squash-merge if the guard allows")
    s.add_argument("pr", type=int)
    s.set_defaults(fn=cmd_issue_merge)
    s = sub.add_parser("milestone-start", help="create/update milestone issues, branch and PR")
    s.add_argument("milestone", type=int)
    s.add_argument("--dry-run", action="store_true", help="print actions; write nothing")
    s.set_defaults(fn=cmd_milestone_start)
    s = sub.add_parser("milestone-pr", help="roll merged issue PRs into the milestone PR body")
    s.add_argument("milestone", type=int)
    s.add_argument("--dry-run", action="store_true", help="print the new body; don't edit the PR")
    s.set_defaults(fn=cmd_milestone_pr)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args))
    except Refusal as r:
        for reason in r.reasons:
            print(f"✗ {reason}")
        return 1
    except (gh.GhError, FileNotFoundError, OSError) as e:
        print(f"✗ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
