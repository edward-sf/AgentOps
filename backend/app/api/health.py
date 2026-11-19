import asyncio

from fastapi import APIRouter, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

router = APIRouter()

HEALTH_TIMEOUT_SECONDS = 2


@router.get("/health")
async def health(request: Request, response: Response) -> dict[str, str]:
    engine: AsyncEngine = request.app.state.engine
    try:
        async with asyncio.timeout(HEALTH_TIMEOUT_SECONDS), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        response.status_code = 503
        return {"status": "degraded", "database": "error"}
    return {"status": "ok", "database": "ok"}
