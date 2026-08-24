"""Modular agent memory for LangGraph agents — short-term + long-term.

Drop this `memory/` folder into any LangGraph project. Two tiers, both exposed as
standard LangGraph interfaces so they work with *any* agent:

    from memory import MemoryConfig, build_checkpointer, build_store, setup_store
    from memory import make_memory_tools, recall, remember

    cfg = MemoryConfig.from_env()

    # SHORT-TERM: a checkpointer (per-thread conversation state)
    checkpointer = build_checkpointer(conn)            # conn from your PG/Lakebase pool

    # LONG-TERM: a store (durable, cross-thread memories); None if disabled
    store = build_store(cfg); setup_store(store)

    # Wire into ANY LangGraph agent:
    graph = build_graph(checkpointer=checkpointer)     # your StateGraph, or
    agent = create_react_agent(llm, tools, checkpointer=checkpointer, store=store)

See README.md for full wiring patterns (prebuilt ReAct agent, custom StateGraph,
and tool-calling), backends, and configuration.
"""
from .config import MemoryConfig
from .short_term import build_checkpointer, setup_checkpointer
from .long_term import (
    build_store,
    setup_store,
    make_memory_tools,
    recall,
    remember,
    scope_for,
)

__all__ = [
    "MemoryConfig",
    "build_checkpointer",
    "setup_checkpointer",
    "build_store",
    "setup_store",
    "make_memory_tools",
    "recall",
    "remember",
    "scope_for",
]
