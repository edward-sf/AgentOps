"""Review verdict comments: `Review verdict: PASS @ <40-char head SHA>`."""

import re
from dataclasses import dataclass
from typing import Literal, cast

Result = Literal["PASS", "FAIL"]
_FIRST_LINE = re.compile(r"^Review verdict: (PASS|FAIL) @ ([0-9a-f]{40})$")


@dataclass(frozen=True)
class Verdict:
    result: Result
    sha: str


def format_verdict(result: Result, sha: str, findings: str) -> str:
    body = f"Review verdict: {result} @ {sha}"
    findings = findings.strip()
    return f"{body}\n\n{findings}\n" if findings else f"{body}\n"


def parse_verdict(body: str) -> Verdict | None:
    lines = body.splitlines()
    if not lines:
        return None
    m = _FIRST_LINE.match(lines[0])
    return Verdict(cast(Result, m.group(1)), m.group(2)) if m else None


def latest_verdict_for(comments: list[str], sha: str) -> Verdict | None:
    """The newest verdict whose SHA equals `sha`. `comments` are oldest first."""
    for body in reversed(comments):
        v = parse_verdict(body)
        if v is not None and v.sha == sha:
            return v
    return None
