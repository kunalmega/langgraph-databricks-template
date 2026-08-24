"""Short-term memory: a LangGraph checkpointer (per-thread conversation state).

A checkpointer persists the graph's state for a given `thread_id`, so the agent
remembers the current conversation across turns. This is the "short-term" tier:
scoped to one thread/session.

Backends:
  - "postgres" → langgraph PostgresSaver, using a psycopg connection you provide
    (e.g. borrowed from a Lakebase pool per request). Durable, multi-replica safe.
  - "memory"   → InMemorySaver: process-local, lost on restart. Great for dev/tests.

Usage (any LangGraph agent):
    from memory import build_checkpointer
    with pool.connection() as conn:
        cp = build_checkpointer(conn)                 # postgres
        graph = build_graph(checkpointer=cp)
        graph.invoke(payload, config={"configurable": {"thread_id": tid}})
"""
from __future__ import annotations

from typing import Any, Optional


def build_checkpointer(conn: Optional[Any] = None, backend: str = "postgres"):
    """Return a LangGraph checkpointer.

    Args:
        conn: an open psycopg connection (required for "postgres"). Typically
            borrowed from a connection pool for the lifetime of one request.
        backend: "postgres" (default) or "memory".
    """
    if backend == "memory":
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver()

    if backend == "postgres":
        if conn is None:
            raise ValueError(
                "build_checkpointer(backend='postgres') requires an open psycopg "
                "`conn` (e.g. from your Lakebase pool). For dev/tests pass "
                "backend='memory'."
            )
        from langgraph.checkpoint.postgres import PostgresSaver
        return PostgresSaver(conn)

    raise ValueError(f"Unknown short-term backend: {backend!r} (use 'postgres' or 'memory').")


def setup_checkpointer(conn: Any) -> None:
    """One-time DDL: create the checkpoint tables on a Postgres/Lakebase connection.

    Idempotent. CREATE INDEX CONCURRENTLY cannot run in a transaction, so the
    connection is switched to autocommit for the call.
    """
    from langgraph.checkpoint.postgres import PostgresSaver
    prev = conn.autocommit
    conn.autocommit = True
    try:
        PostgresSaver(conn).setup()
    finally:
        conn.autocommit = prev
