# Development Workflow

How work is planned, tracked, and merged in this repo. These rules apply to every contributor, human or AI.

## Planning artifacts

| Artifact | Lives in | Purpose |
| -------- | -------- | ------- |
| Design spec | `docs/specs/` | *What* we build and why. Source of truth for requirements. |
| Roadmap | `docs/plans/*-roadmap.md` | Milestones and their issues. |
| Milestone plan | `docs/plans/*-m<N>-*.md` | *How* each issue is implemented: files, interfaces, tests. Written when the milestone starts, not before. |
| GitHub Issues + Project | GitHub | *Status*. The single source of truth for what is in progress, blocked, or done. |

Plans hold decisions; GitHub holds status. Don't track status by editing plan checkboxes alone.

## GitHub structure

- **Milestones = spec build slices:** [`M0 - Architecture and project setup`](https://github.com/edward-sf/AgentOps/milestone/1), [`M1 - Agent execution and tracing`](https://github.com/edward-sf/AgentOps/milestone/2), [`M2 - Evaluation engine`](https://github.com/edward-sf/AgentOps/milestone/3), [`M3 - Scoring and evaluation analytics`](https://github.com/edward-sf/AgentOps/milestone/4), [`M4 - Dashboard and trace viewer`](https://github.com/edward-sf/AgentOps/milestone/5), [`M5 - Model comparison and regression detection`](https://github.com/edward-sf/AgentOps/milestone/6), [`M6 - Monitoring and cost analytics`](https://github.com/edward-sf/AgentOps/milestone/7), [`M7 - Deployment and portfolio demonstration`](https://github.com/edward-sf/AgentOps/milestone/8).
- **Issues = one plan task each.** The title starts with its plan ID, e.g. `M1.T3 Harmonize datasources`. The issue body links the plan section and lists its acceptance criteria.
- **Project board [AgentOps](https://github.com/users/edward-sf/projects/10)**, Assigness: `@edward-sf`, Status: `Future → Ready → In progress → In review → Merged to milestone → Done`

## Branch model

```
main ◄──(milestone PR, author reviews, merge commit)── milestone/m<N> ◄──(issue PRs, squash)── <issue-number>-<short-slug>
```

- `main` only receives milestone PRs. It is always deployable.
- `milestone/m<N>` is the integration branch for one milestone. It's created from `main` when the milestone starts and deleted after it merges.
- Issue branches `<issue-number>-<short-slug>` are cut from the current milestone branch, and each one's PR targets that branch.

## Lifecycle of a milestone

1. **Start.** Write the milestone's detailed plan. Reconcile its issues to match, and move them to `Ready`. Create `milestone/m<N>` from up-to-date `main`, push it, and open a draft milestone PR `milestone/m<N> → main` right away, so the chain of work collects in one place as issue PRs land. Add to [AgentOps](https://github.com/users/edward-sf/projects/10) and the corresponding milestone in GitHub and assign `@edward-sf`.
2. **Issues.** Work through the issues following the lifecycle below.
3. **Milestone review.** When every issue is `Merged to milestone`, run the full checks on the milestone branch and fill in the milestone PR template. That means listing every issue PR, adding a `Closes #...` line for every issue, and including verification evidence. Then mark the PR ready for review.
4. **Merge.** The author reviews and approves. They then merge with a **merge commit**, not squash, so the per-issue commits stay on `main`. GitHub closes the issues, and the board moves them to `Done`.

## Lifecycle of an issue

1. **Ready.** The milestone plan exists and the issue body matches its task. Only `Ready` issues get picked up.
2. **In progress.** Create a branch from the up-to-date milestone branch, named `<issue-number>-<short-slug>`, e.g. `7-acs-source`. One issue per branch.
3. **Implement with TDD.** Follow the plan's steps: write a failing test, then make it pass, then commit. Small commits are fine on the branch.
4. **In review.** Open a PR into `milestone/m<N>` using the issue PR template. The PR title is in Conventional Commit form (`feat(pipeline): add ACS source`). The body says `Part of #<n>`, not `Closes`: closing keywords don't fire on non-default branches, so they live in the milestone PR. It also notes any deviation from the plan.
5. **Merged to milestone.** The `ci` gate is green and a PASS review record exists at head, and then it's squash-merged into the milestone branch. The squashed commit message is the PR title. Delete the branch and move the issue to `Merged to milestone`. When the milestone has an agent workflow run, the merge also needs a passing gate record and a passing review record for the PR's current head commit (see Agent workflow below).

## Agent workflow

Agents run milestones with the `./flow` CLI, which scripts every deterministic step and enforces the rules below. **If this document or a skill disagrees with `./flow`, `./flow` is authoritative.** Precedence: `./flow` > CONTRIBUTING.md > skills and AGENTS.md. Run `./flow <command> --help` for exact flags.

### Roles

- **Orchestrator:** the root agent session. It owns the milestone: it dispatches Implementers (one per issue), watches CI, makes sure every issue PR gets a scoped review, and merges.
- **Implementer:** a subagent dispatched for one issue. It runs `./flow issue-start`, works with TDD, runs `make check`, and opens the PR with `./flow issue-pr`.
- **Reviewer:** a fresh subagent with no implementation context. It reviews a PR against the issue's plan section and acceptance criteria only, and **returns** a verdict, the reviewed SHA and a findings file. The Orchestrator dispatches one for every issue PR, whoever implemented it, once `pr-gate` is green. Reviewers never post, merge or label.

### Records required before merge

An issue PR is squash-merged into `milestone/m<N>` only with both records on its **current head commit**:

1. **Gate record:** the `ci` workflow is green on head (`./flow pr-gate <pr>`).
2. **Review record:** a PR comment, posted by the Orchestrator after the gate is green, scoped to the issue's plan section and acceptance criteria. Its first line is machine-checked:

   ```
   Review verdict: PASS @ <40-char head SHA>
   ```

   (or `FAIL`), followed by the findings. The Orchestrator posts it with `./flow post-verdict <pr> PASS|FAIL <findings-file> --sha <sha>`. The SHA is the one printed under `## Head` by `./flow review-context`, and `post-verdict` refuses if the PR head has moved since.

`./flow issue-merge <pr>` merges only if all of these hold. It merges with `--match-head-commit`, so a push that lands mid-merge is refused too.

- `ci` is green on head.
- The latest verdict at head is PASS.
- The branch matches the issue (`<n>-<slug>`).
- Any checkpoint on the issue is approved.
- The PR base is a `milestone/*` branch.

### Rules

- **SHA invalidation.** Any new push invalidates the gate and the verdict. Re-run `pr-gate`, then get a new review.
- **FAIL loop.** A FAIL blocks the merge. The fix is pushed (the Implementer is resumed with the findings), the PR passes the gate again, and a scoped re-review of the fix diff posts a new verdict.
- **Minor findings** that a Reviewer defers are not fixed in the PR. They get backlog issues at milestone review (see "New work gets an issue" under Rules).
- **Checkpoints.** A task marked `**Checkpoint**` in the plan needs the author's sign-off before any task that depends on it starts. Approval is the `checkpoint-approved` label on the checkpoint *issue*, applied **only by the author**. Agents never apply it. A GitHub review approval is impossible, because every PR is opened under the author's GitHub account and GitHub does not let authors approve their own PRs. At a checkpoint the Orchestrator posts the verdict plus a short "checkpoint review guide" comment listing the open decisions, notifies the author, and carries on with independent tasks.
- **Board statuses** follow `Future → Ready → In progress → In review → Merged to milestone → Done`. `./flow` moves issues through them.

### Plan task format

`flow` parses plans in `docs/plans/`. Each task has a `### M<N>.T<n> <title>` heading, an optional `**Depends on:** M<N>.T<n>, …` line, an optional `**Checkpoint**` line (the whole line, unindented), then `**Goal:**` and `**Acceptance criteria**`. Copy `docs/plans/TEMPLATE-milestone-plan.md`.

### Skills

Role procedures live in `.claude/skills/`: `orchestrate-milestone` (the Orchestrator's loop) and `review-issue-pr` (the Reviewer's procedure). `AGENTS.md` is the short command reference every agent loads.

## Rules

- **`main` is always deployable.** No direct pushes, and only milestone PRs merge into it. `ci` must pass on both issue PRs and milestone PRs.
- **The author owns `main`.** AI agents may open, update, and squash-merge issue PRs into a milestone branch once the `ci` gate is green and a PASS review record exists at head. Only the author approves and merges milestone PRs into `main` and closes `checkpoint` issues.
- **Keep milestone branches current.** If `main` changes during a milestone (rare, e.g. a hotfix), merge `main` into the milestone branch. Don't rebase it, because that would rewrite history other PRs are based on.
- **Hotfixes.** An urgent fix to `main` outside a milestone gets its own issue and a PR straight to `main`, which the author reviews.
- **Deviations go in writing.** If implementation must differ from the plan, say so in the PR. If it differs from the spec, update the spec in the same PR, or open a `type:docs` issue first if the change is large enough to discuss.
- **Checkpoints are blocking.** Issues that require author review must receive approval before subsequent tasks can start. Notify the author when checkpoints require their attention.
- **New work gets an issue.** Bugs found along the way get a `type:bug` issue. Address them immediately if blocking other milestone tasks. Otherwise, address them at the end of the current milestone. Similarly, deferred minor issues discovered by the final reviewer must have backlog issues created to address them. These should be assigned to existing, open milestones to which they logically belong. If no such milestone exists, the minor issue *must* be addressed in the current milestone.
- **Plans are just-in-time.** When a milestone starts, write its detailed plan, then reconcile its issues (edit, split, add, close) so IDs match the plan's tasks before any issue moves to `Ready`.

## Definition of done

Per issue PR; the milestone PR re-checks across the whole slice.

- [ ] Tests added or updated, and they pass locally and in CI. Tests run against real Postgres, not mocks.
- [ ] Backend: `ruff check`, `ruff format --check`, `mypy` and `pytest` pass.
- [ ] Frontend: `eslint`, `tsc`, `vitest` and `next build` pass. The first three are in `make check`. `next build` runs in CI.
- [ ] `make check` is green. It mirrors `ci` except `next build` and image builds, and prints only failures.
- [ ] Generated API types are up to date (`make api-types`, from M0.T8) whenever the API schema changed.
- [ ] The issue's acceptance criteria are met
- [ ] Spec, plan, or README updated if behavior or decisions changed
- [ ] No raw or exported data committed (only test fixtures)

## Local commands

Run from the repo root. Rows marked "(from M0.T<n>)" arrive with that task.

| Command | Does |
| ------- | ---- |
| `make setup` | Install backend (`uv sync`) and frontend (`npm ci`) dependencies |
| `make db` | Start Postgres (`pgvector/pgvector:pg17`) in Docker and wait until healthy |
| `make migrate` | Apply database migrations (from M0.T3) |
| `make seed` | Load sample data (from M0.T7) |
| `make dev-api` | Run the API with reload on `:8000` |
| `make dev-web` | Run the Next.js dev server |
| `make dev` | Run `dev-api` and `dev-web` together |
| `make test` | Run backend and frontend tests |
| `make lint` | Run ruff and eslint |
| `make typecheck` | Run mypy and `tsc` |
| `make fmt` | Auto-fix with ruff |
| `make check` | Run every check `ci` runs (`scripts/check.sh`); prints only failures |
| `make api-types` | Regenerate the frontend's API types (from M0.T8) |
| `make up` | Run the whole stack in Docker Compose (from M0.T9) |
| `make down` | Stop Docker Compose services |

`./flow` commands (one line each; `./flow <command> --help` for flags):

| Command | Does |
| ------- | ---- |
| `./flow status [N]` | One row per milestone issue: board status, branch, PR, CI, verdict |
| `./flow milestone-start <N>` | Create or update issues, board entries, milestone branch and draft PR from the plan |
| `./flow issue-start <n>` | Check dependencies, branch `<n>-<slug>`, mark In progress |
| `./flow issue-pr <n> "<title>" [--summary TEXT] [--notes FILE]` | Push and open the issue PR, mark In review |
| `./flow pr-gate <pr>` | Wait for `ci` on head; print failing logs |
| `./flow review-context <pr>` | Print task, criteria, head SHA and diff |
| `./flow post-verdict <pr> PASS\|FAIL <file> --sha <sha>` | Comment the verdict at the reviewed head |
| `./flow issue-merge <pr>` | Squash-merge if the guard allows |
| `./flow milestone-pr <N>` | Roll merged issue PRs into the milestone PR body |
