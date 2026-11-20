"""Helpers shared by the author-only commands that drive the API in process (`make smoke`,
`make eval`)."""

from typing import Any

from httpx import AsyncClient

AGENT_NAME = "weather-bot"
VERSION = 1
RESEED_HINT = (
    "hint: this database was seeded before M1; reset the dev DB volume, "
    "then run 'make db migrate seed' (see README)"
)


class CliError(Exception):
    """A request the command needed failed. The message says which and why."""


def ok(response: Any, what: str) -> Any:
    """The response's JSON, or `CliError` naming `what` if the status isn't 2xx."""
    if response.status_code >= 300:
        raise CliError(f"{what}: HTTP {response.status_code} {response.text}")
    return response.json()


def is_reseed_needed(response: Any) -> bool:
    """A 422 naming an unsupported parameter: the database was seeded before M1."""
    return response.status_code == 422 and "unsupported parameter" in str(
        response.json().get("detail")
    )


async def find_id_by_name(client: AsyncClient, path: str, name: str) -> str:
    """The id of the item called `name` in the paginated list at `path`.

    Raises `CliError` if there is none, with a hint to run `make seed`.
    """
    offset = 0
    while True:
        page = ok(await client.get(path, params={"limit": 200, "offset": offset}), f"list {path}")
        for item in page["items"]:
            if item["name"] == name:
                return str(item["id"])
        offset += len(page["items"])
        if not page["items"] or offset >= page["total"]:
            raise CliError(f"{name!r} not found at {path}; run 'make seed'")


async def get_version(client: AsyncClient) -> dict[str, Any]:
    """The seeded `weather-bot` v1."""
    agent_id = await find_id_by_name(client, "/api/v1/agents", AGENT_NAME)
    version: dict[str, Any] = ok(
        await client.get(f"/api/v1/agents/{agent_id}/versions/{VERSION}"),
        f"get {AGENT_NAME} v{VERSION}",
    )
    return version
