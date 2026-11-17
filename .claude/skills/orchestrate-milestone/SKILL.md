---
name: orchestrate-milestone
description: Use when you are the root agent running an AgentOps milestone end to end: dispatching Implementers, getting each issue PR reviewed, handling checkpoints, and finishing the milestone PR.
---

# Orchestrate a milestone

You are the Orchestrator (see CONTRIBUTING.md, "Agent workflow"). For the general practice of dispatching and managing subagents, follow `superpowers:subagent-driven-development`. This skill covers only what is specific to this repo. Precedence: `./flow` > CONTRIBUTING.md > this skill.

## Setup

1. Confirm the milestone plan exists in `docs/plans/` in the task format of `TEMPLATE-milestone-plan.md`.
2. `./flow milestone-start <N>` (use `--dry-run` first). It is safe to re-run.
3. `./flow status <N>` shows where every issue stands.

## The loop, per issue

Pick the next `Ready` issue whose dependencies are merged (and approved, if they are checkpoints). Independent issues can run in parallel.

1. **Dispatch an Implementer.** The prompt must contain:
   - the issue number and the plan path and section (`### M<N>.T<n>`)
   - the plan's Global Constraints
   - "Use `./flow issue-start <n>`, TDD, `make check` green, then `./flow issue-pr <n> "<conventional title>" --summary "..."`; add `--notes FILE` for deviations"
   - "Do not run `post-verdict` or `issue-merge`, and do not comment or label anything"
   - the report format you want back, and that the Implementer may not dispatch subagents
2. **Gate.** `./flow pr-gate <pr>`. If red, resume the Implementer with the failing output, and gate again. Dispatch no review until it is green.
3. **Review.** Once the gate is green, dispatch a fresh Reviewer subagent for every issue PR, whoever implemented it. Give it the review package: the diff (exclude lockfiles), the task brief, and the Implementer's report, and tell it to follow `.claude/skills/review-issue-pr`. If you implemented the issue yourself, a fresh Reviewer is mandatory.
4. **Verdict.** The Reviewer returns a verdict, the reviewed SHA and a findings-file path. You post it: `./flow post-verdict <pr> PASS|FAIL <file> --sha <sha>`. Reviewers never post, merge or label.
5. **FAIL loop.** Resume the Implementer with the findings. After the push, run `pr-gate` again, then dispatch a scoped re-review that checks the fix diff only against the findings. Every push needs a fresh verdict at the new head.
6. **Merge.** `./flow issue-merge <pr>`. Do not work around a refusal. Fix its stated reason.
7. **Minor findings.** Deferred minor findings are not fixed in the PR. Keep a list. At milestone review, open a backlog issue for each, assigned to an existing open milestone where it belongs (CONTRIBUTING, "New work gets an issue").

## Checkpoints

A task with `**Checkpoint**` needs author approval before dependents start.

1. After PASS, post the verdict plus a short "checkpoint review guide" comment on the PR, listing the open decisions the author must make.
2. Notify the author, and continue with independent tasks.
3. Do not merge or start dependents until the checkpoint issue carries the `checkpoint-approved` label. **Never apply that label yourself.** `issue-merge` and `issue-start` check it.

## Finishing

1. When `./flow status <N>` shows every issue `Merged to milestone`, run `git checkout milestone/m<N> && git pull`, then `make setup db` and a full `make check`.
2. `./flow milestone-pr <N>`, then write the summary, verification evidence and deviations sections of the milestone PR.
3. Create the backlog issues for deferred minor findings.
4. Mark the milestone PR ready for review. The author reviews and merges it. Never merge into `main` yourself.
