from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.config import (
    ALLOWED_PARAMETERS,
    DEFAULT_MAX_STEPS,
    AgentConfig,
    InvalidParametersError,
    validate_parameters,
)
from app.agents.tools import UnknownToolError
from tests.factories import make_agent_version


def test_constants() -> None:
    assert {"max_steps", "max_tokens"} == ALLOWED_PARAMETERS
    assert DEFAULT_MAX_STEPS == 10


def test_defaults() -> None:
    params = validate_parameters({})
    assert params.max_steps == 10
    assert params.model_params == {"max_tokens": 1024}


def test_max_steps_removed_from_model_params() -> None:
    params = validate_parameters({"max_steps": 3, "max_tokens": 512})
    assert params.max_steps == 3
    assert params.model_params == {"max_tokens": 512}


@pytest.mark.parametrize("key", ["temperature", "temprature", "top_p", "top_k"])
def test_unknown_key_rejected(key: str) -> None:
    with pytest.raises(InvalidParametersError, match=key):
        validate_parameters({key: 0})


@pytest.mark.parametrize("value", [0, -1, "5", 2.5, True, None])
def test_bad_max_steps_rejected(value: Any) -> None:
    with pytest.raises(InvalidParametersError, match="max_steps"):
        validate_parameters({"max_steps": value})


@pytest.mark.parametrize("value", [0, -1, "5", 2.5, True, None])
def test_bad_max_tokens_rejected(value: Any) -> None:
    with pytest.raises(InvalidParametersError, match="max_tokens"):
        validate_parameters({"max_tokens": value})


async def test_from_version(db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session, parameters={"max_steps": 4})
    config = AgentConfig.from_version(version)
    assert config.agent_version_id == version.id
    assert config.provider == "anthropic"
    assert config.model == version.model
    assert config.system_prompt == version.system_prompt
    assert config.params.max_steps == 4
    assert config.params.model_params == {"max_tokens": 1024}
    assert [t.spec.name for t in config.tools] == ["get_weather"]


async def test_from_version_unknown_tool(db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session, tools=["get_weather", "search"])
    with pytest.raises(UnknownToolError, match="search"):
        AgentConfig.from_version(version)


async def test_from_version_bad_parameters(db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session, parameters={"temperature": 0.2})
    with pytest.raises(InvalidParametersError, match="temperature"):
        AgentConfig.from_version(version)
