# M<N> — <Milestone name>: Implementation Plan

<!-- Skeleton for a milestone plan. Copy to docs/plans/<date>-m<N>-<slug>.md (the `-m<N>-` in
the file name is what `flow` uses to find the plan; this template deliberately has none).
`flow milestone-start <N>` reads this format and creates one GitHub issue per task heading. -->

**Goal:** One or two sentences on what the milestone delivers.

**Spec:** [`docs/specs/<date>-m<N>-<slug>-design.md`](../specs/<date>-m<N>-<slug>-design.md)

## Global Constraints

<!-- Rules every task must follow. They are repeated in each task brief. -->

- Constraint one.
- Constraint two.

## Review Focus

<!-- What reviewers should check hardest, with the task that tests each. -->

1. **Risky behaviour:** the expected outcome. (Test in M<N>.T1.)

---

## Tasks

<!-- Format contract for each task:
  - Heading `### M<N>.T<k> <Title>`; the title becomes the issue title after the ID.
  - `**Depends on:**` followed by task IDs (M<N>.T<j>, comma separated) or `none`.
  - `**Checkpoint**` alone on a line marks the task as needing the author's
    `checkpoint-approved` label before dependents start. Omit it otherwise.
  - `**Goal:**` first paragraph becomes the issue's Goal.
  - `**Acceptance criteria**` followed by `- [ ]` items (nested bullets allowed).
  - Tasks are separated by a `---` line. Text inside code fences is ignored. -->

### M<N>.T1 First task

**Depends on:** none

**Goal:** What this task delivers, in one paragraph.

**Acceptance criteria**
- [ ] First observable criterion.
- [ ] Second observable criterion.

**Files:**
- Create: `path/to/new_file.py`
- Modify: `path/to/existing_file.py`

---

### M<N>.T2 Second task, a checkpoint

**Depends on:** M<N>.T1
**Checkpoint**

**Goal:** What this task delivers.

**Acceptance criteria**
- [ ] A criterion.
- [ ] The author approves the PR (`checkpoint-approved` label) before dependents start.
