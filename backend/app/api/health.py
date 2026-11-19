import asyncio
import logging

from fastapi import APIRouter, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger("app.health")

router = APIRouter()

HEALTH_TIMEOUT_SECONDS = 2


@router.get("/health")
async def health(request: Request, response: Response) -> dict[str, str]:
    engine: AsyncEngine = request.app.state.engine
    try:
        async with asyncio.timeout(HEALTH_TIMEOUT_SECONDS), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        logger.warning("health check: database unreachable", exc_info=True)
        response.status_code = 503
        return {"status": "degraded", "database": "error"}
    return {"status": "ok", "database": "ok"}
