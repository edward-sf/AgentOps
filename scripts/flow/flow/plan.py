"""Parse a milestone plan into tasks."""

import re
from dataclasses import dataclass
from pathlib import Path

_HEADING = re.compile(r"^### (M(\d+)\.T(\d+)) (.+?)\s*$")
_TASK_ID = re.compile(r"^(M\d+\.T\d+)\b")
_ANY_ID = re.compile(r"M\d+\.T\d+")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


@dataclass(frozen=True)
class Task:
    id: str
    milestone: int
    number: int
    title: str
    body: str
    acceptance: list[str]
    checkpoint: bool
    depends_on: list[str]


def task_id_from_title(title: str) -> str | None:
    m = _TASK_ID.match(title.strip())
    return m.group(1) if m else None


def find_plan(milestone: int, root: Path) -> Path:
    matches = sorted((root / "docs" / "plans").glob(f"*-m{milestone}-*.md"))
    if len(matches) != 1:
        raise FileNotFoundError(
            f"expected exactly one docs/plans/*-m{milestone}-*.md, found {len(matches)}"
        )
    return matches[0]


def _unfenced(lines: list[str]) -> list[tuple[str, bool]]:
    """Pair each line with whether it sits outside a code fence."""
    out: list[tuple[str, bool]] = []
    fence: str | None = None
    for line in lines:
        m = _FENCE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                out.append((line, False))
            else:
                out.append((line, True))
        else:
            out.append((line, False))
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
    return out


def _parse_section(heading: re.Match[str], lines: list[tuple[str, bool]]) -> Task:
    checkpoint = False
    depends: list[str] = []
    acceptance: list[str] = []
    in_acceptance = False
    for line, live in lines:
        stripped = line.strip()
        if not live:
            in_acceptance = False
            continue
        if stripped == "**Checkpoint**":
            checkpoint = True
        if line.startswith("**Depends on:**"):
            depends = _ANY_ID.findall(line[len("**Depends on:**") :])
        if line.startswith("**Acceptance criteria**"):
            in_acceptance = True
            continue
        if in_acceptance:
            if re.match(r"^- \[[ xX]\] ", line):
                acceptance.append(line[6:].strip())
            elif stripped and line[0] in " \t" and acceptance:
                acceptance[-1] += " " + stripped.removeprefix("- ")
            elif stripped:
                in_acceptance = False
    return Task(
        id=heading.group(1),
        milestone=int(heading.group(2)),
        number=int(heading.group(3)),
        title=heading.group(4),
        body="\n".join(line for line, _ in lines).strip("\n"),
        acceptance=acceptance,
        checkpoint=checkpoint,
        depends_on=depends,
    )


def parse_plan(text: str) -> list[Task]:
    tasks: list[Task] = []
    current: re.Match[str] | None = None
    section: list[tuple[str, bool]] = []

    def close() -> None:
        if current is not None:
            tasks.append(_parse_section(current, section))

    for line, live in _unfenced(text.splitlines()):
        if live and (line.startswith("### ") or line.strip() == "---"):
            close()
            current = _HEADING.match(line) if line.startswith("### ") else None
            section = []
            continue
        if current is not None:
            section.append((line, live))
    close()
    return tasks
