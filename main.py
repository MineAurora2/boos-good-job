"""FastAPI application composition and executable server entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import threading
import webbrowser

from fastapi import FastAPI

from app.security import HybridAuthMiddleware, SecurityPolicy
from app.state import STATE
from app.routes import control_router, delivery_router, shutdown_introduce_jobs


DASHBOARD_URL = 'http://127.0.0.1:47999/dashboard'


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
    application.add_middleware(HybridAuthMiddleware, policy=SecurityPolicy.from_env())
    application.include_router(delivery_router)
    application.include_router(control_router)

    @application.get('/api/connection', summary='Test the authenticated backend connection')
    async def connection_status():
        return {
            'ok': True,
            'connected': True,
            'status': 'ok',
            'scriptApiVersion': 1,
            'serverTime': datetime.now(timezone.utc).isoformat(),
        }

    return application


app = create_app()


def open_dashboard_when_started(
    server: object,
    stop_event: threading.Event,
    *,
    opener=None,
    poll_interval: float = 0.05,
) -> bool:
    """Open the dashboard only after this Uvicorn server binds successfully."""
    opener = opener or webbrowser.open
    while not stop_event.is_set():
        if getattr(server, 'should_exit', False):
            return False
        if getattr(server, 'started', False):
            try:
                return bool(opener(DASHBOARD_URL, new=2, autoraise=True))
            except webbrowser.Error:
                return False
        stop_event.wait(poll_interval)
    return False


def start_dashboard_opener(
    server: object,
    stop_event: threading.Event,
) -> threading.Thread:
    """Watch one server instance without blocking its main event loop."""
    thread = threading.Thread(
        target=open_dashboard_when_started,
        args=(server, stop_event),
        name='dashboard-opener',
        daemon=True,
    )
    thread.start()
    return thread


def run_server() -> None:
    """Run the local API and open its dashboard once this server is listening."""
    import uvicorn

    config = uvicorn.Config(
        app,
        host='0.0.0.0',
        port=47999,
        reload=False,
        proxy_headers=False,
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(config)
    stop_event = threading.Event()
    opener_thread = start_dashboard_opener(server, stop_event)
    try:
        server.run()
    finally:
        stop_event.set()
        opener_thread.join(timeout=1)


if __name__ == '__main__':
    run_server()
