"""Lakebase (Postgres) connection pool with per-connection OAuth tokens.

Follows the official Databricks Apps pattern: a custom psycopg Connection class
mints a fresh database credential each time the pool opens a physical connection,
so tokens are always valid. max_lifetime=2700 recycles connections 15 min before
the 1-hour token expiry.
"""
import os

import psycopg
from psycopg_pool import ConnectionPool

from .config import get_workspace_client

_w = get_workspace_client()


class OAuthConnection(psycopg.Connection):
    """psycopg connection that generates a fresh Lakebase OAuth token per connect."""

    @classmethod
    def connect(cls, conninfo="", **kwargs):
        endpoint_name = os.environ["ENDPOINT_NAME"]
        credential = _w.postgres.generate_database_credential(endpoint=endpoint_name)
        kwargs["password"] = credential.token
        return super().connect(conninfo, **kwargs)


def _build_pool() -> ConnectionPool:
    # In a Databricks App these are auto-injected when the DB resource is attached.
    # Locally, config the env vars (see README) or rely on PGUSER = current user email.
    host = os.environ["PGHOST"]
    port = os.environ.get("PGPORT", "5432")
    database = os.environ.get("PGDATABASE", "langgraph_app")
    user = os.environ["PGUSER"]
    sslmode = os.environ.get("PGSSLMODE", "require")

    return ConnectionPool(
        conninfo=f"dbname={database} user={user} host={host} port={port} sslmode={sslmode}",
        connection_class=OAuthConnection,
        min_size=1,
        max_size=10,
        max_lifetime=2700,  # 45 min — recycle before 1-hour token expiry
        open=False,  # opened in FastAPI lifespan
    )


# Module-level pool; opened in app lifespan.
pool = _build_pool()
