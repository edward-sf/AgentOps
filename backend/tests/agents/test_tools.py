import json

import pytest

from app.agents.tools import TOOLS, UnknownToolError, get_tools, get_weather


async def test_seattle_weather() -> None:
    result = json.loads(await get_weather({"location": "seattle"}))
    assert result == {
        "location": "seattle",
        "temperature_c": 12,
        "conditions": "light rain",
        "raining": True,
    }


async def test_london_weather_case_insensitive() -> None:
    result = json.loads(await get_weather({"location": "LONDON"}))
    assert result == {
        "location": "LONDON",
        "temperature_c": 9,
        "conditions": "overcast",
        "raining": False,
    }


async def test_default_weather() -> None:
    result = json.loads(await get_weather({"location": "Paris"}))
    assert result == {
        "location": "Paris",
        "temperature_c": 18,
        "conditions": "clear",
        "raining": False,
    }


async def test_weather_is_deterministic() -> None:
    assert await get_weather({"location": "Seattle"}) == await get_weather({"location": "Seattle"})


@pytest.mark.parametrize("arguments", [{}, {"location": 3}, {"location": ""}])
async def test_missing_location_raises(arguments: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="location"):
        await get_weather(arguments)


def test_registry_spec_matches_contract() -> None:
    spec = TOOLS["get_weather"].spec
    assert spec.name == "get_weather"
    assert spec.description
    assert spec.input_schema == {
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"],
    }


def test_get_tools_returns_in_order() -> None:
    assert [t.spec.name for t in get_tools(["get_weather"])] == ["get_weather"]
    assert get_tools([]) == []


def test_get_tools_unknown_raises() -> None:
    with pytest.raises(UnknownToolError, match="nope") as excinfo:
        get_tools(["get_weather", "nope"])
    assert excinfo.value.name == "nope"
