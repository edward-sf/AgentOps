from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

# scope="function": the session closes (and commits) when the endpoint returns,
# before the response is sent. The default "request" scope commits afterwards.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
