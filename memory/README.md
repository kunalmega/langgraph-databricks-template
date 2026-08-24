# `memory/` — modular agent memory for LangGraph

Drop this folder into **any** LangGraph agent to add memory. Two independent tiers,
both exposed as the **standard LangGraph interfaces**, so nothing here is tied to a
particular agent or app:

| Tier | LangGraph interface | What it is | Backend here |
|---|---|---|---|
| **Short-term** | `checkpointer` | Per-**thread** conversation state (this chat) | `PostgresSaver` (Lakebase) / `InMemorySaver` |
| **Long-term** | `store` (`BaseStore`) | Durable, **cross-thread** memories about the user | `DatabricksStore` (Managed Agent Memory, Lakebase-backed, semantic) / `InMemoryStore` |

> Short-term = "what did we just say?" (one thread). Long-term = "what do I know
> about this user?" (every thread). LangGraph keeps them as two separate concepts —
> a checkpointer and a store — and so do we.

## Install

No extra packages beyond what a Databricks LangGraph agent already uses:
`langgraph`, `langgraph-checkpoint-postgres`, and (for long-term) `databricks-langchain`.

Copy the `memory/` folder into your project. That's it.

## Configure (all optional; env-driven)

| Env var | Default | Meaning |
|---|---|---|
| `MEMORY_SHORT_TERM` | `postgres` | `postgres` or `memory` |
| `MEMORY_LONG_TERM` | `none` | `databricks`, `memory`, or `none` |
| `LAKEBASE_INSTANCE` | — | Lakebase instance name (or use project+branch) |
| `LAKEBASE_PROJECT` / `LAKEBASE_BRANCH` | — / `production` | Lakebase project + branch for the store |
| `MEMORY_PG_SCHEMA` | — | Postgres schema for the store tables |
| `MEMORY_EMBEDDING_ENDPOINT` | — | Databricks embeddings endpoint for **semantic** search, e.g. `databricks-gte-large-en` |
| `MEMORY_EMBEDDING_DIMS` | — | Embedding dimensions, e.g. `1024` |

Or construct `MemoryConfig(...)` directly — no env needed.

## Wire it into your agent

**1) Prebuilt ReAct agent (tool-calling)** — long-term as tools the model can call:

```python
from memory import MemoryConfig, build_checkpointer, build_store, setup_store, make_memory_tools

cfg = MemoryConfig.from_env()
store = build_store(cfg); setup_store(store)
checkpointer = build_checkpointer(conn)            # conn from your Lakebase pool

tools = [*my_tools, *make_memory_tools(store, scope_getter=lambda: caller_email)]
agent = create_react_agent(llm, tools, checkpointer=checkpointer, store=store)
```

**2) Custom `StateGraph`** — long-term via node helpers (`recall` / `remember`):

```python
from memory import recall, remember

def respond(state, config, store):                 # LangGraph injects `store`
    user = config["configurable"]["caller_email"]
    memories = recall(store, user, query=state["question"])   # [] if disabled
    # ... build the prompt with `memories`, call the LLM ...
    remember(store, user, "prefers spicy food")               # no-op if disabled

graph = builder.compile(checkpointer=checkpointer, store=store)
```

**3) Just the checkpointer** (short-term only): pass `build_checkpointer(conn)` and
skip the store.

## Scope & safety (best practices)

- **Scope from trusted context.** `recall`/`remember`/the tools namespace memories
  under `(*prefix, scope)` where `scope` is a server-side identity (user email/id).
  Never pass model-generated text as the scope.
- `recall`/`remember` **never raise** — memory must not break a turn. They no-op
  when long-term is disabled (`store is None`), so the same code runs in every
  environment.
- Prefer short, self-describing memories; `search` before writing to avoid
  duplicates; pass a stable `key` to `remember(...)` to update a known fact.

## Provisioning the long-term store (Databricks)

`DatabricksStore` stores memories in **Lakebase** and (optionally) builds a semantic
index with a Databricks embeddings endpoint. Point `LAKEBASE_PROJECT`/`LAKEBASE_INSTANCE`
at your Lakebase, set `MEMORY_LONG_TERM=databricks`, and (optionally)
`MEMORY_EMBEDDING_ENDPOINT`. `setup_store(store)` creates the tables on first run.

## Files

| File | What it is |
|---|---|
| `config.py` | `MemoryConfig` — env-driven, fully overridable. |
| `short_term.py` | `build_checkpointer` / `setup_checkpointer` (the checkpointer tier). |
| `long_term.py` | `build_store` / `setup_store`, `recall` / `remember`, `make_memory_tools` (the store tier). |
| `__init__.py` | Public API. |
