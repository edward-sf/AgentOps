from collections.abc import Callable
from functools import partial
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.providers import provider_for
from app.agents.providers.base import Provider
from app.db import get_session

# scope="function": the session closes (and commits) when the endpoint returns,
# before the response is sent. The default "request" scope commits afterwards.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]

ProviderFactory = Callable[[str], Provider]


def get_provider_factory(request: Request) -> ProviderFactory:
    """Builds the provider named by an AgentVersion. Tests override this dependency."""
    return partial(provider_for, settings=request.app.state.settings)


ProviderFactoryDep = Annotated[ProviderFactory, Depends(get_provider_factory)]
