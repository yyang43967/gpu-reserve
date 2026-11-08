from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api import (
    API_PREFIX,
    auth,
    health,
    labs,
    me,
    reservations,
    search,
    servers,
    stats,
    uploads,
    users,
)
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.db.session import create_engine, create_session_factory
from app.services.storage import ObjectStorage, S3Storage

# Exactly what the frontend sends, so a page on an allowed origin cannot use any other
# method or custom header. CORS-safelisted headers such as Accept are always allowed.
CORS_ALLOWED_METHODS: Final = ("GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS")
CORS_ALLOWED_HEADERS: Final = ("Authorization", "Content-Type")


def create_app(settings: Settings | None = None, storage: ObjectStorage | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        redis = Redis.from_url(str(settings.redis_url))
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.redis = redis
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(title="GPU Reserve API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    # Building the clients does no I/O, so it happens here rather than in the lifespan.
    app.state.storage = storage or S3Storage(settings)

    # Registered before CORS so that CORS wraps the error middleware and 500s stay readable.
    register_error_handlers(app, settings.problem_base_url)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=CORS_ALLOWED_METHODS,
        allow_headers=CORS_ALLOWED_HEADERS,
    )

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(me.router, prefix=API_PREFIX)
    app.include_router(users.router, prefix=API_PREFIX)
    app.include_router(labs.router, prefix=API_PREFIX)
    app.include_router(servers.router, prefix=API_PREFIX)
    app.include_router(reservations.router, prefix=API_PREFIX)
    app.include_router(search.router, prefix=API_PREFIX)
    app.include_router(stats.router, prefix=API_PREFIX)
    app.include_router(uploads.router, prefix=API_PREFIX)

    return app


app = create_app()
