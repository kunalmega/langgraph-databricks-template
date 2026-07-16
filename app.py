"""FastAPI entry point for the Databricks App.

Opens the Lakebase connection pool on startup, mounts the chat API under /api,
and serves the built React SPA.
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.db import pool
from server.routes import chat


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open(wait=True, timeout=30.0)  # fail fast if Lakebase is unreachable
    yield
    pool.close()


app = FastAPI(title="LangGraph Sample Agent", lifespan=lifespan)
app.include_router(chat.router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


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
