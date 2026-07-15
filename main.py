"""FastAPI application composition and executable server entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.state import STATE
from app.routes import control_router, delivery_router, shutdown_introduce_jobs


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize configuration, SQLite storage, and one-time legacy migration."""
    STATE.startup()
    try:
        yield
    finally:
        await shutdown_introduce_jobs()


def create_app() -> FastAPI:
    """Build the API from the two domain-level routers."""
    application = FastAPI(lifespan=lifespan)
    application.include_router(delivery_router)
    application.include_router(control_router)
    return application


app = create_app()


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(
        'main:app',
        host='0.0.0.0',
        port=47999,
        reload=False,
        timeout_graceful_shutdown=3,
    )
