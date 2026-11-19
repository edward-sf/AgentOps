"""The tool registry: tools are code, looked up by name from an AgentVersion (spec D5)."""

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from app.agents.providers.base import ToolSpec


class UnknownToolError(LookupError):
    """An AgentVersion names a tool that isn't in the registry."""

    def __init__(self, name: str) -> None:
        super().__init__(f"unknown tool: {name}")
        self.name = name


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    func: Callable[[dict[str, Any]], Awaitable[str]]


_WEATHER: dict[str, tuple[int, str, bool]] = {
    "seattle": (12, "light rain", True),
    "london": (9, "overcast", False),
}
_DEFAULT_WEATHER = (18, "clear", False)


async def get_weather(arguments: dict[str, Any]) -> str:
    """Fixed, made-up weather. No network call, so runs differ only by the agent (D5)."""
    location = arguments.get("location")
    if not isinstance(location, str) or not location:
        raise ValueError("location is required")
    temperature_c, conditions, raining = _WEATHER.get(location.lower(), _DEFAULT_WEATHER)
    return json.dumps(
        {
            "location": location,
            "temperature_c": temperature_c,
            "conditions": conditions,
            "raining": raining,
        }
    )


TOOLS: Mapping[str, Tool] = MappingProxyType(
    {
        "get_weather": Tool(
            spec=ToolSpec(
                name="get_weather",
                description="Get the current weather for a location.",
                input_schema={
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                },
            ),
            func=get_weather,
        ),
    }
)


def get_tools(names: Sequence[str]) -> list[Tool]:
    """The registry's tools for `names`, in order. Raises `UnknownToolError`."""
    tools: list[Tool] = []
    for name in names:
        tool = TOOLS.get(name)
        if tool is None:
            raise UnknownToolError(name)
        tools.append(tool)
    return tools
