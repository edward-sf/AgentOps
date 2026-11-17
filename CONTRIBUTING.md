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
5. **Merged to milestone.** Checks pass, the task gets a self-review against its plan section, and then it's squash-merged into the milestone branch. The squashed commit message is the PR title. Delete the branch and move the issue to `Merged to milestone`. When the milestone has an agent workflow run, the merge also needs a passing gate record and a passing review record for the PR's current head commit (see Agent workflow below).

## Agent workflow

<-- Complete this section during [M0 - Architecture and project setup](https://github.com/edward-sf/AgentOps/milestone/1) -->

## Rules

- **`main` is always deployable.** No direct pushes, and only milestone PRs merge into it. `ci` must pass on both issue PRs and milestone PRs.
- **The author owns `main`.** AI agents may open, update, and squash-merge issue PRs into a milestone branch once checks pass and they've self-reviewed against the plan. Only the author approves and merges milestone PRs into `main` and closes `checkpoint` issues.
- **Keep milestone branches current.** If `main` changes during a milestone (rare, e.g. a hotfix), merge `main` into the milestone branch. Don't rebase it, because that would reqrite history other PRs are based on.
- **Hotfixes.** An urgent fix to `main` outside a milestone gets its own issue and a PR straight to `main`, which the author reviews.
- **Deviations go in writing.** If implementation must differ from the plan, say so in the PR. If it differs from the spec, update the spec in the same PR, or open a `type:docs` issue first if the change is large enough to discuss.
- **Checkpoints are blocking.** Issues that require author review must receive approval before subsequent tasks can start. Notify the author when checkpoints require their attention.
- **New work gets an issue.** Bugs found along the way get a `type:bug` issue. Address them immediately if blocking other milestone tasks.Otherwise, address them at the end of the current milestone. Similarly, deferred minor issues discovered by the final reviewer must have backlog issues created to address them. These should be assigned to existing, open milestones to which they logically belong. If no such milestone exists, the minor issue *must* be addressed in the current milestone.
- **Plans are just-in-time.** When a milestone starts, write its detailed plan, then reconcile its issues (edit, split, add, close) so IDs match the plan's tasks before any issue moves to `Ready`.

## Definition of done

Per issue PR; the milestone PR re-checks across the whole slice.

<-- Complete this section during [M0 - Architecture and project setup](https://github.com/edward-sf/AgentOps/milestone/1) -->

- [ ] Tests added or updated, and they pass locally and in CI
- [ ] `ruff check`, `ruff format --check`, `mypy`, `eslint`, etc. (Complete as tooling decisions are settled)
- [ ] The issue's acceptance criteria are met
- [ ] Spec, plan, or README updated if behavior or decisions changed
- [ ] No raw or exported data committed (only test fixtures)

## Local commands

<-- Complete this section during [M0 - Architecture and project setup](https://github.com/edward-sf/AgentOps/milestone/1) -->
