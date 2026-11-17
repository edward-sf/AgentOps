# AGENTS.md

Instructions for AI coding agents working in this repo. Humans: start with [README.md](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md). This file summarizes them for agents and does not replace them. If the two disagree, CONTRIBUTING wins. If a doc and `./flow` disagree, `./flow` wins.

## Roles

- **Orchestrator** (root session): owns the milestone, dispatches Implementers, makes sure every issue PR is reviewed, merges. Skill: `.claude/skills/orchestrate-milestone`.
- **Implementer** (subagent, one issue): `./flow issue-start <n>` → TDD → `make check` → `./flow issue-pr`. Never merges.
- **Reviewer** (fresh subagent): reviews one PR against its plan section only. Skill: `.claude/skills/review-issue-pr`.

## `./flow` (run from repo root; `--help` for flags)

- `status [N]`: one row per milestone issue (board, branch, PR, CI, verdict).
- `milestone-start <N>`: create or update issues, branch and draft PR from the plan.
- `issue-start <n>`: check dependencies, branch `<n>-<slug>`, mark In progress.
- `issue-pr <n> "<title>" [--summary TEXT] [--notes FILE]`: push, open the PR, mark In review.
- `pr-gate <pr>`: wait for `ci` on head; prints failing logs.
- `review-context <pr>`: print task, criteria, `## Head` SHA and diff.
- `post-verdict <pr> PASS|FAIL <findings-file> --sha <sha>`: comment the verdict. Refuses if head moved.
- `issue-merge <pr>`: squash-merge if the guard allows.
- `milestone-pr <N>`: roll merged issue PRs into the milestone PR.

## `make`

- `make setup`: install dependencies. `make db` / `make down`: start / stop Postgres.
- `make dev`, `dev-api`, `dev-web`: run the app. `make up`: full Docker stack (from M0.T9).
- `make migrate` (from M0.T3), `make seed` (from M0.T7), `make api-types` (from M0.T8).
- `make test`, `lint`, `typecheck`, `fmt`: the individual checks.
- `make check`: every check `ci` runs; prints only failures. Must be green before `issue-pr`.

## Merge rule

An issue PR is squash-merged into `milestone/m<N>` by `./flow issue-merge <pr>` only when `ci` is green on head, the latest verdict at head is `Review verdict: PASS @ <head SHA>`, the branch is `<n>-<slug>`, any checkpoint is approved, and the base is `milestone/*`. Any new push invalidates the gate and verdict: re-run `pr-gate`, then get a fresh review. A FAIL blocks the merge until fixed and re-reviewed.

## Hard rules

- **Never apply the `checkpoint-approved` label.** Only the author does. Never approve or merge a milestone PR into `main`.
- Never push to `main` or a `milestone/*` branch directly. One issue per branch.
- Implementers and Reviewers do not run `post-verdict` or `issue-merge` unless the Orchestrator told them to.
- Reviewers judge only the issue's plan section and acceptance criteria.
- Deviations from the plan go in the PR (`--notes`). Deferred minor findings get backlog issues.
- Respect the Global Constraints in the plan (Python 3.12 via uv, npm, `/api/v1`, real Postgres in tests).

## Where things are

Specs `docs/specs/`, plans `docs/plans/` (task format: `TEMPLATE-milestone-plan.md`), workflow detail [CONTRIBUTING.md](CONTRIBUTING.md#agent-workflow).
