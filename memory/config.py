"""Configuration for the modular agent-memory package.

Everything is env-driven with safe defaults, and every value can be overridden by
constructing MemoryConfig(...) directly — so the package stays portable across
agents and never depends on a specific app's settings module.

Two independent tiers (see the package README):
  - SHORT-TERM  → a LangGraph *checkpointer* (per-thread conversation state).
  - LONG-TERM   → a LangGraph *store* (cross-thread, durable memories).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Tuple


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass
class MemoryConfig:
    # --- Short-term (checkpointer) ------------------------------------------
    # "postgres" → PostgresSaver (needs a psycopg connection at build time)
    # "memory"   → InMemorySaver (dev/tests; state lost on restart)
    short_term: str = "postgres"

    # --- Long-term (store) --------------------------------------------------
    # "databricks" → DatabricksStore (Lakebase-backed, optional semantic search)
    # "memory"     → InMemoryStore (dev/tests)
    # "none"       → no long-term memory (store is None; helpers are no-ops)
    long_term: str = "none"

    # Namespace prefix for a memory scope: memories live under
    # (*namespace_prefix, <scope>) — e.g. ("memories", "user@acme.com").
    namespace_prefix: Tuple[str, ...] = ("memories",)

    # --- Databricks long-term (DatabricksStore over Lakebase) ---------------
    # Provide EITHER a Lakebase instance name, OR project (+ branch).
    lakebase_instance: str | None = None
    lakebase_project: str | None = None
    lakebase_branch: str = "production"
    lakebase_schema: str | None = None  # Postgres schema for the store tables

    # Optional semantic search: a Databricks embeddings endpoint + its dims.
    # If unset, the store falls back to non-semantic lookup.
    embedding_endpoint: str | None = None   # e.g. "databricks-gte-large-en"
    embedding_dims: int | None = None       # e.g. 1024 for gte-large-en
    embedding_fields: Tuple[str, ...] = ("memory",)  # which value fields to embed

    @classmethod
    def from_env(cls) -> "MemoryConfig":
        """Build from environment variables (all optional; safe defaults)."""
        dims = _env("MEMORY_EMBEDDING_DIMS")
        return cls(
            short_term=_env("MEMORY_SHORT_TERM", "postgres"),
            long_term=_env("MEMORY_LONG_TERM", "none"),
            lakebase_instance=_env("LAKEBASE_INSTANCE"),
            lakebase_project=_env("LAKEBASE_PROJECT"),
            lakebase_branch=_env("LAKEBASE_BRANCH", "production"),
            lakebase_schema=_env("MEMORY_PG_SCHEMA"),
            embedding_endpoint=_env("MEMORY_EMBEDDING_ENDPOINT"),
            embedding_dims=int(dims) if dims else None,
        )

    @property
    def long_term_enabled(self) -> bool:
        return self.long_term not in (None, "", "none")
