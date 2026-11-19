"""Run an AgentVersion synchronously (spec D4, 6.2)."""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.config import AgentConfig, InvalidParametersError
from app.agents.providers import ProviderNotConfiguredError, UnsupportedProviderError
from app.agents.providers.base import Provider
from app.agents.runtime import RunOutcome, run_agent
from app.agents.tools import UnknownToolError
from app.errors import NotFoundError, ServiceUnavailableError, UnprocessableError
from app.models import AgentVersion
from app.schemas.run import RunCreate


async def start_run(
    session: AsyncSession, provider_factory: Callable[[str], Provider], payload: RunCreate
) -> RunOutcome:
    """Validate the version, then run it and store its trace.

    Configuration errors raise (404, 422, 503) before anything is written. A run that fails
    is returned as a `failed` outcome. No transaction is open while the model is called.
    """
    version = await session.get(AgentVersion, payload.agent_version_id)
    if version is None:
        raise NotFoundError("Agent version not found")
    try:
        config = AgentConfig.from_version(version)
        provider = provider_factory(config.provider)
    except (InvalidParametersError, UnknownToolError, UnsupportedProviderError) as exc:
        raise UnprocessableError(str(exc)) from exc
    except ProviderNotConfiguredError as exc:
        raise ServiceUnavailableError(str(exc)) from exc
    # End the read transaction: a run may take a minute and must not hold a connection
    # in a transaction while it waits on the model. Ingest begins a new one.
    await session.commit()
    return await run_agent(session=session, provider=provider, config=config, input=payload.input)
