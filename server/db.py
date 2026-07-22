"""Lakebase (Postgres) connection pool with per-connection OAuth tokens.

Follows the official Databricks Apps pattern: a custom psycopg Connection class
mints a fresh database credential each time the pool opens a physical connection,
so tokens are always valid. max_lifetime=2700 recycles connections 15 min before
the 1-hour token expiry.

Construction is behind explicit factories (`create_pool`, `get_pool`) rather than
happening at import time. That keeps importing this module side-effect free — so
tests can import it without a live workspace, and startup can validate config and
build the pool deliberately in the FastAPI lifespan.
"""
from functools import lru_cache
from typing import Optional

import psycopg
from psycopg_pool import ConnectionPool

from .config import get_workspace_client
from .settings import get_settings


class OAuthConnection(psycopg.Connection):
    """psycopg connection that generates a fresh Lakebase OAuth token per connect."""

    @classmethod
    def connect(cls, conninfo="", **kwargs):
        settings = get_settings()
        # Client is resolved lazily, per physical connect — not at import time.
        client = get_workspace_client()
        credential = client.postgres.generate_database_credential(
            endpoint=settings.endpoint_name
        )
        kwargs["password"] = credential.token
        return super().connect(conninfo, **kwargs)


def create_pool(*, open: bool = False) -> ConnectionPool:
    """Build a Lakebase connection pool, validating required config first.

    Kept as an explicit factory so callers control WHEN the pool is built (e.g.
    the FastAPI lifespan) and tests can inject their own.
    """
    settings = get_settings()
    # Fail fast with one clear message if the state store is not configured.
    settings.require("endpoint_name", "pghost", "pguser")

    conninfo = (
        f"dbname={settings.pgdatabase} user={settings.pguser} "
        f"host={settings.pghost} port={settings.pgport} sslmode={settings.pgsslmode}"
    )
    return ConnectionPool(
        conninfo=conninfo,
        connection_class=OAuthConnection,
        min_size=1,
        max_size=10,
        max_lifetime=2700,  # 45 min — recycle before 1-hour token expiry
        open=open,
    )


_pool: Optional[ConnectionPool] = None


def get_pool() -> ConnectionPool:
    """Return the process-wide pool, creating it on first use.

    The FastAPI lifespan opens it explicitly; this getter is what request
    handlers use so they never touch module-load-time state.
    """
    global _pool
    if _pool is None:
        _pool = create_pool(open=False)
    return _pool


def set_pool(pool: Optional[ConnectionPool]) -> None:
    """Override the process-wide pool (used by the lifespan and by tests)."""
    global _pool
    _pool = pool
