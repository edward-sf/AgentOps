"""Thin wrappers over the `gh` CLI. All network access goes through here."""

import json as _json
import subprocess
from typing import Any


class GhError(Exception):
    pass


def run(args: list[str], *, input: str | None = None, allow: tuple[int, ...] = (0,)) -> str:
    """Run `gh <args>` and return stdout. Raises GhError unless the exit code is in `allow`."""
    try:
        proc = subprocess.run(["gh", *args], input=input, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise GhError("the gh CLI is not installed") from e
    if proc.returncode not in allow:
        detail = (proc.stderr or proc.stdout).strip()
        raise GhError(f"gh {' '.join(args[:3])} failed ({proc.returncode}): {detail}")
    return proc.stdout


def json(args: list[str], *, allow: tuple[int, ...] = (0,)) -> Any:
    out = run(args, allow=allow)
    try:
        return _json.loads(out)
    except ValueError as e:
        raise GhError(f"gh {' '.join(args[:3])} returned invalid JSON") from e


def graphql(query: str, **variables: object) -> dict[str, Any]:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        flag = "-f" if isinstance(value, str) else "-F"
        text = str(value).lower() if isinstance(value, bool) else str(value)
        args += [flag, f"{key}={text}"]
    result = json(args)
    if not isinstance(result, dict):
        raise GhError("unexpected GraphQL response")
    if result.get("errors"):
        raise GhError(f"GraphQL error: {result['errors']}")
    data = result.get("data")
    return data if isinstance(data, dict) else {}
