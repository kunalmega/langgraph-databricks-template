"""Long-term memory: a LangGraph store (durable, cross-thread memories).

A LangGraph *store* (BaseStore) holds memories that outlive a single conversation
and are shared across threads, organized by a hierarchical **namespace** + key:

    store.put(("memories", "user@acme.com"), key, {"memory": "prefers paneer"})
    store.search(("memories", "user@acme.com"), query="what do they like?")

Backends:
  - "databricks" → DatabricksStore (Databricks Managed Agent Memory, Lakebase-backed),
    with optional semantic search via a Databricks embeddings endpoint.
  - "memory"     → InMemoryStore (dev/tests), optionally semantic if given an index.
  - "none"       → returns None; the helpers below become safe no-ops.

Because the return value is a standard LangGraph BaseStore, it drops into ANY
LangGraph agent: `create_react_agent(..., store=store)` or
`graph.compile(..., store=store)`. Nodes then receive `store` by parameter name.

Best practices baked in (see the Databricks Managed Memory docs):
  - **Scope from trusted context.** `scope_for(...)` derives the namespace from a
    server-side identity (user email / id) — never from model output.
  - Store short, self-describing memories; use `search` before writing to avoid
    near-duplicates; keep a stable key when updating a known fact.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Callable, List, Optional, Sequence, Tuple

from .config import MemoryConfig


# --- Namespacing -------------------------------------------------------------

def _sanitize(scope: str) -> str:
    """Make a scope safe/stable as a LangGraph namespace label.

    LangGraph namespace labels must be non-empty and cannot contain '.', so we
    collapse anything outside [A-Za-z0-9_-] to '-' (e.g. an email like
    'a.b@acme.com' → 'a-b-acme-com'). Deterministic, so the same identity always
    maps to the same namespace.
    """
    return re.sub(r"[^A-Za-z0-9_-]", "-", (scope or "anon").strip()) or "anon"


def scope_for(prefix: Sequence[str], scope: str) -> Tuple[str, ...]:
    """Namespace tuple for a memory scope: (*prefix, sanitized-scope)."""
    return (*tuple(prefix), _sanitize(scope))


# --- Store factory -----------------------------------------------------------

def build_store(config: Optional[MemoryConfig] = None):
    """Return a LangGraph BaseStore for long-term memory, or None if disabled.

    Call `.setup()` on the returned store once before first use (see setup_store()).
    """
    config = config or MemoryConfig.from_env()

    if not config.long_term_enabled:
        return None

    if config.long_term == "memory":
        from langgraph.store.memory import InMemoryStore
        # If an embedding endpoint is configured we could pass an index here; the
        # in-memory backend is meant for dev, so keep it simple and non-semantic.
        return InMemoryStore()

    if config.long_term == "databricks":
        from databricks_langchain import DatabricksStore
        kwargs: dict[str, Any] = {}
        if config.lakebase_instance:
            kwargs["instance_name"] = config.lakebase_instance
        else:
            kwargs["project"] = config.lakebase_project
            kwargs["branch"] = config.lakebase_branch
        if config.lakebase_schema:
            kwargs["schema"] = config.lakebase_schema
        if config.embedding_endpoint:
            kwargs["embedding_endpoint"] = config.embedding_endpoint
            if config.embedding_dims:
                kwargs["embedding_dims"] = config.embedding_dims
            kwargs["embedding_fields"] = list(config.embedding_fields)
        return DatabricksStore(**kwargs)

    raise ValueError(
        f"Unknown long-term backend: {config.long_term!r} "
        "(use 'databricks', 'memory', or 'none')."
    )


def setup_store(store) -> None:
    """One-time table creation for stores that need it (idempotent, best-effort)."""
    if store is None:
        return
    setup = getattr(store, "setup", None)
    if callable(setup):
        setup()


# --- Node helpers (for custom StateGraph agents) -----------------------------

def recall(store, scope: str, query: str, *, prefix: Sequence[str] = ("memories",),
           limit: int = 5) -> List[str]:
    """Return up to `limit` remembered strings for `scope` relevant to `query`.

    Safe no-op (returns []) when store is None. Never raises — recall must never
    break a turn.
    """
    if store is None:
        return []
    try:
        items = store.search(scope_for(prefix, scope), query=query, limit=limit)
        return [
            (it.value or {}).get("memory") or ""
            for it in items
            if (it.value or {}).get("memory")
        ]
    except Exception:
        return []


def remember(store, scope: str, content: str, *, prefix: Sequence[str] = ("memories",),
             key: Optional[str] = None) -> None:
    """Persist a durable memory string for `scope`.

    Pass a stable `key` to update a known fact instead of minting a duplicate;
    omit it for a new memory. Safe no-op when store is None; never raises.
    """
    if store is None or not content:
        return
    try:
        store.put(scope_for(prefix, scope), key or str(uuid.uuid4()), {"memory": content})
    except Exception:
        pass


# --- Tools (for tool-calling / ReAct agents) ---------------------------------

def make_memory_tools(store, scope_getter: Callable[[], str],
                      *, prefix: Sequence[str] = ("memories",)):
    """Return [save_memory, search_memory] LangGraph tools bound to `store`.

    `scope_getter` is a zero-arg callable returning the trusted scope (e.g. the
    caller's email) at call time — so the tools never let the model choose scope.
    Add them to any tool-calling agent: create_react_agent(llm, [*tools, ...]).
    Returns [] when store is None.
    """
    if store is None:
        return []
    from langchain_core.tools import tool

    @tool
    def save_memory(memory: str) -> str:
        """Save a durable fact about the user (preferences, constraints, context) to recall in future conversations."""
        remember(store, scope_getter(), memory, prefix=prefix)
        return "Saved."

    @tool
    def search_memory(query: str) -> str:
        """Recall durable facts previously saved about the user, relevant to the query."""
        hits = recall(store, scope_getter(), query, prefix=prefix)
        return "\n".join(f"- {h}" for h in hits) if hits else "(no relevant memories)"

    return [save_memory, search_memory]
