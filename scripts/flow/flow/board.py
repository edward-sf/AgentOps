"""Project board status updates. The board is advisory: failures warn, never raise."""

import sys
from typing import Any

from flow import gh
from flow.config import OWNER, PROJECT_NUMBER, REPO


STATUS_LIMIT = 500


def find_option(options: list[dict[str, Any]], status: str) -> dict[str, Any] | None:
    wanted = status.strip().lower()
    for option in options:
        if str(option.get("name", "")).strip().lower() == wanted:
            return option
    return None


def _warn(message: str) -> None:
    print(f"warning: board: {message}", file=sys.stderr)


def set_status(issue_number: int, status: str) -> None:
    try:
        url = f"https://github.com/{OWNER}/{REPO}/issues/{issue_number}"
        project = str(PROJECT_NUMBER)
        # item-add is idempotent: it returns the existing item when already on the board.
        item = gh.json(
            ["project", "item-add", project, "--owner", OWNER, "--url", url, "--format", "json"]
        )
        view = gh.json(["project", "view", project, "--owner", OWNER, "--format", "json"])
        fields = gh.json(["project", "field-list", project, "--owner", OWNER, "--format", "json"])
        field = next((f for f in fields["fields"] if f.get("name") == "Status"), None)
        if field is None:
            _warn("project has no Status field; not updated")
            return
        option = find_option(field.get("options", []), status)
        if option is None:
            _warn(f"no Status option named '{status}'; #{issue_number} not updated")
            return
        gh.run(
            [
                "project",
                "item-edit",
                "--id",
                item["id"],
                "--project-id",
                view["id"],
                "--field-id",
                field["id"],
                "--single-select-option-id",
                option["id"],
            ]
        )
    except (gh.GhError, KeyError, TypeError) as e:
        _warn(f"could not set #{issue_number} to '{status}': {e}")


def try_statuses() -> dict[int, str] | None:
    """Issue number -> board status, or None when the board could not be read."""
    try:
        data = gh.json(
            [
                "project",
                "item-list",
                str(PROJECT_NUMBER),
                "--owner",
                OWNER,
                "--format",
                "json",
                "--limit",
                str(STATUS_LIMIT),
            ]
        )
        if len(data["items"]) >= STATUS_LIMIT:
            _warn(f"board has {STATUS_LIMIT} or more items; the read may be truncated")
            return None
        return {
            int(i["content"]["number"]): i["status"]
            for i in data["items"]
            if i.get("status") and i.get("content", {}).get("number")
        }
    except (gh.GhError, KeyError, TypeError, ValueError) as e:
        _warn(f"could not read statuses: {e}")
        return None


def statuses() -> dict[int, str]:
    """Issue number -> board status. Empty on failure (advisory)."""
    return try_statuses() or {}
