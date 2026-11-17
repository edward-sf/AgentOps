---
name: review-issue-pr
description: Use when you are asked to review an AgentOps issue PR and post a PASS or FAIL verdict. It is the Reviewer's procedure, scoped to the issue's plan section and acceptance criteria.
---

# Review an issue PR

You are a Reviewer: a fresh agent with no implementation context. Review one PR against its own issue only. `./flow` is authoritative: if this skill disagrees with it, trust `./flow`.

## Procedure

1. `./flow review-context <pr>`. It prints the plan section, acceptance criteria, the head SHA under `## Head` (a 40-hex SHA), and the diff. Record the SHA: it is the commit you are reviewing.
2. Confirm the gate: `./flow pr-gate <pr>` must exit 0. If `ci` is red or pending, stop and report. Do not review a red PR.
3. Check scope. Every change in the diff must serve the plan section. Flag unrelated changes. Read the changed files when the diff is not enough, and run the tests if that helps you judge.
4. Check each acceptance criterion against the code and tests, one by one.
5. Check the Global Constraints from the plan and the brief.
6. Write findings to a file (for example in the scratchpad). Each finding names the file and line, what is wrong, and why. Start with a one-line summary.
7. Decide the verdict, then post it: `./flow post-verdict <pr> PASS|FAIL <findings-file> --sha <the SHA from step 1>`. It refuses if the head moved since. In that case, run `review-context` again, re-review the new commits and post at the new SHA. Only post if the Orchestrator told you to. Otherwise return the verdict, the SHA and the findings file to it.

## FAIL or PASS

**FAIL** if any of these is true:
- an acceptance criterion is unmet
- a Global Constraint is broken
- stated behaviour has no test
- there is a correctness bug

**PASS** otherwise. Everything else is a **minor finding**: style, naming, small cleanups, speculative hardening, nice-to-have tests. List minor findings in a "Deferred (minor)" section of the findings file. They do not block the merge. The Orchestrator turns them into backlog issues on the right open milestone at milestone review. Do not fail a PR for them, and do not file issues yourself unless told to.

## Re-review after a FAIL

Review only the fix diff (the new commits) against your earlier findings, and check that nothing new broke. Post a new verdict at the new head SHA.

## Do not

- Review beyond the issue's scope, or ask for changes the plan did not call for.
- Edit code, push, merge, or apply labels. Never apply `checkpoint-approved`.
- Post a verdict for a SHA other than the one you reviewed.
