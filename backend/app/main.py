from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import agents, datasets, health
from app.config import Settings, get_settings
from app.errors import register_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # create_async_engine is lazy: no connection is made until first use.
        engine = create_async_engine(settings.database_url)
        app.state.engine = engine
        app.state.sessionmaker = async_sessionmaker[AsyncSession](engine, expire_on_commit=False)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="AgentOps", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(agents.router)
    app.include_router(datasets.router)
    return app


app = create_app()
