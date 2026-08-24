"""Process-wide handle to the long-term memory store.

Built once at app startup (app.py lifespan) from the modular `memory/` package and
read by the chat route. Mirrors the connection-pool accessor pattern in db.py.

Long-term memory is OFF unless MEMORY_LONG_TERM is configured, so by default
get_store() returns None and the agent behaves exactly as a short-term-only app.
"""
from __future__ import annotations

from typing import Optional

_store = None


def set_store(store) -> None:
    global _store
    _store = store


def get_store():
    return _store


def init_long_term_memory() -> Optional[object]:
    """Build + set up the long-term store from env config. Best-effort: a failure
    degrades to no long-term memory rather than crashing startup."""
    import logging

    from memory import MemoryConfig, build_store, setup_store

    log = logging.getLogger("memory")
    cfg = MemoryConfig.from_env()
    if not cfg.long_term_enabled:
        set_store(None)
        return None
    try:
        store = build_store(cfg)
        setup_store(store)
        set_store(store)
        log.info("long-term memory enabled (backend=%s)", cfg.long_term)
        return store
    except Exception as exc:  # noqa: BLE001
        log.warning("long-term memory disabled (init failed): %s", exc)
        set_store(None)
        return None
