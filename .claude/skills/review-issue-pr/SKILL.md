---
name: review-issue-pr
description: Use when you are asked to review an AgentOps issue PR and return a PASS or FAIL verdict. It is the Reviewer's procedure, scoped to the issue's plan section and acceptance criteria.
---

# Review an issue PR

You are a Reviewer: a fresh agent with no implementation context. Review one PR against its own issue only. The Orchestrator has already run `pr-gate`, so the PR is green. Precedence: `./flow` > CONTRIBUTING.md > this skill.

## Procedure

1. `./flow review-context <pr>`. It prints the plan section, acceptance criteria, the head SHA under `## Head` (a 40-hex SHA), and the diff. Record the SHA: it is the commit you are reviewing. If `## Head` is missing from the output, stop and report that. Do not guess a SHA. "The brief" below means the task's plan section plus the plan's Global Constraints section.
2. Check scope. Every change in the diff must serve the plan section. Flag unrelated changes. When the diff is not enough, read whole files at the PR head with `git show <head-sha>:<path>`, so the local checkout does not matter. You may run tests to judge (a focused `uv run pytest <path>` in `backend/`, or `make test`), but never edit code.
3. Check each acceptance criterion against the code and tests, one by one.
4. Check the Global Constraints in the brief.
5. Write the findings file (for example in the scratchpad). Line 1 is a one-line summary. Then `## Blocking` (each FAIL reason, with file:line, what is wrong and why), then `## Deferred (minor)`.
6. **Return** to the Orchestrator: the verdict, the reviewed SHA from step 1, and the findings-file path. The Orchestrator posts it with `./flow post-verdict`. You never post, merge or label. If the Orchestrator says the head moved, run `review-context` again and re-review at the new SHA.

## FAIL or PASS

**FAIL** if any of these is true:
- an acceptance criterion is unmet
- a Global Constraint is broken
- a behaviour the acceptance criteria or plan steps state has no test covering it (extra coverage beyond that is minor)
- there is a correctness bug

**PASS** otherwise. Everything else is a **minor finding**: style, naming, small cleanups, speculative hardening, extra test coverage beyond what the criteria state. List minor findings in a "Deferred (minor)" section of the findings file. They do not block the merge. The Orchestrator turns them into backlog issues on the right open milestone at milestone review. Do not fail a PR for them, and do not file issues yourself unless told to.

## Re-review after a FAIL

Review only the fix diff (the new commits) against your earlier findings, and check that nothing new broke. Post a new verdict at the new head SHA.

## Do not

- Review beyond the issue's scope, or ask for changes the plan did not call for.
- Edit code, push, merge, post verdicts, or apply labels. Never apply `checkpoint-approved`.
- Return a SHA other than the one you reviewed.
