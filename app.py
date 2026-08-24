"""FastAPI entry point for the Databricks App.

Opens the Lakebase connection pool on startup, mounts the chat API under /api,
and serves the web UI. Adds request-correlation middleware and separate
liveness/readiness health checks for production operability.
"""
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server.db import create_pool, get_pool, set_pool
from server.routes import chat
from server.settings import get_settings

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate config and build the pool deliberately at startup (not import time).
    pool = create_pool(open=False)
    set_pool(pool)
    pool.open(wait=True, timeout=30.0)  # fail fast if Lakebase is unreachable
    # Long-term memory store (no-op unless MEMORY_LONG_TERM is configured).
    from server.memory_wire import init_long_term_memory, set_store
    init_long_term_memory()
    try:
        yield
    finally:
        set_store(None)
        pool.close()
        set_pool(None)


app = FastAPI(title="LangGraph Sample Agent", lifespan=lifespan)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Attach a request id for correlation, echo it back, and log the boundary.

    Honors an inbound X-Request-ID (e.g. from a gateway/load balancer) so a trace
    id can flow end-to-end; otherwise generates one.
    """
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    logger.info("request.start id=%s %s %s", request_id, request.method, request.url.path)
    try:
        response: Response = await call_next(request)
    except Exception:
        logger.exception("request.error id=%s", request_id)
        raise
    response.headers["X-Request-ID"] = request_id
    logger.info("request.end id=%s status=%s", request_id, response.status_code)
    return response


app.include_router(chat.router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    """Liveness: the process is up and serving. Cheap, no dependencies."""
    return {"status": "ok"}


@app.get("/api/ready")
def ready() -> Response:
    """Readiness: verify critical dependencies before taking traffic.

    Checks Lakebase connectivity (SELECT 1) and that the LLM endpoint is
    configured. Returns 503 with per-dependency detail if anything is not ready.
    """
    settings = get_settings()
    checks: dict[str, str] = {}
    ok = True

    try:
        with get_pool().connection() as conn:
            conn.execute("SELECT 1")
        checks["lakebase"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["lakebase"] = f"error: {type(exc).__name__}"
        ok = False

    if settings.llm_endpoint:
        checks["llm_endpoint"] = "configured"
    else:
        checks["llm_endpoint"] = "missing"
        ok = False

    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "not_ready", "checks": checks},
    )


# Serve the web UI. Prefer the built React SPA (frontend/dist) if present;
# otherwise fall back to the no-build static/ page (works without npm).
_base = os.path.dirname(__file__)
_react_dir = os.path.join(_base, "frontend", "dist")
_static_dir = os.path.join(_base, "static")
_ui_dir = _react_dir if os.path.isdir(_react_dir) else _static_dir

if os.path.isdir(_ui_dir):
    _assets = os.path.join(_ui_dir, "assets")
    if os.path.isdir(_assets):
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        return FileResponse(os.path.join(_ui_dir, "index.html"))
