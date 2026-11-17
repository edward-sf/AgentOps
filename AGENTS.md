# AGENTS.md

Instructions for AI coding agents working in this repo. Humans: start with [README.md](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md). This file summarizes them for agents and does not replace them. Precedence: `./flow` > CONTRIBUTING.md > skills and this file.

## Roles

- **Orchestrator** (root session): owns the milestone, dispatches Implementers, runs `pr-gate`, dispatches a fresh Reviewer for every issue PR, posts the verdict, merges. Skill: `.claude/skills/orchestrate-milestone`.
- **Implementer** (subagent, one issue): `./flow issue-start <n>` → TDD → `make check` → `./flow issue-pr`. Never merges.
- **Reviewer** (fresh subagent): reviews one gated PR against its plan section only, and returns verdict, SHA and findings file. Never posts, merges or labels. Skill: `.claude/skills/review-issue-pr`.

## `./flow` (run from repo root; `--help` for flags)

- `status [N]`: one row per milestone issue (board, branch, PR, CI, verdict).
- `milestone-start <N>`: create or update issues, branch and draft PR from the plan.
- `issue-start <n>`: refuses unless the issue is `Ready` and dependencies are merged (and approved); branches `<n>-<slug>`, marks In progress.
- `issue-pr <n> "<title>" [--summary TEXT] [--notes FILE]`: push, open the PR, mark In review.
- `pr-gate <pr>`: wait for `ci` on head; prints failing logs.
- `review-context <pr>`: print task, criteria, `## Head` SHA and diff.
- `post-verdict <pr> PASS|FAIL <findings-file> --sha <sha>`: Orchestrator only. Comments the verdict; refuses if head moved.
- `issue-merge <pr>`: squash-merge if the guard allows.
- `milestone-pr <N>`: roll merged issue PRs into the milestone PR.

## `make`

- `make setup`: install dependencies. `make db` / `make down`: start / stop Postgres.
- `make dev`, `dev-api`, `dev-web`: run the app. `make up`: full Docker stack (from M0.T9).
- `make migrate` (from M0.T3), `make seed` (from M0.T7), `make api-types` (from M0.T8).
- `make test`, `lint`, `typecheck`, `fmt`: the individual checks.
- `make check`: every check `ci` runs; prints only failures. Mirrors `ci` except `next build` and image builds. Must be green before `issue-pr`.

## Merge rule

An issue PR is squash-merged into `milestone/m<N>` by `./flow issue-merge <pr>` only when `ci` is green on head, the latest verdict at head is `Review verdict: PASS @ <head SHA>`, the branch is `<n>-<slug>`, any checkpoint is approved, and the base is `milestone/*`. Any new push invalidates the gate and verdict: re-run `pr-gate`, then get a fresh review. A FAIL blocks the merge until fixed and re-reviewed.

## Implementer checklist

- Your prompt names the plan path and the `### M<N>.T<n>` section. Read that section and the plan's Global Constraints first.
- If `make check` fails, fix it before `issue-pr`.
- If `pr-gate` fails, fix, push to the same branch, and re-run `pr-gate`.
- After a FAIL verdict, push fixes to the same branch. The Orchestrator re-gates and re-reviews.

## Hard rules

- **Never apply the `checkpoint-approved` label.** Only the author does. Never approve or merge a milestone PR into `main`.
- Agents never push directly to `main` or `milestone/*`; only `flow milestone-start` creates a milestone branch. One issue per branch.
- Only the Orchestrator runs `post-verdict` and `issue-merge`.
- Reviewers judge only the issue's plan section and acceptance criteria.
- Deviations from the plan go in the PR (`--notes`). Deferred minor findings get backlog issues.
- Respect the plan's Global Constraints.

## Where things are

Specs `docs/specs/`, plans `docs/plans/` (task format: `TEMPLATE-milestone-plan.md`), workflow detail [CONTRIBUTING.md](CONTRIBUTING.md#agent-workflow).
