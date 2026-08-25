# `memory/` — modular agent memory for LangGraph

Drop this folder into **any** LangGraph agent to add memory. Two independent tiers,
both exposed as the **standard LangGraph interfaces**, so nothing here is tied to a
particular agent or app:

| Tier | LangGraph interface | What it is | Backend here |
|---|---|---|---|
| **Short-term** | `checkpointer` | Per-**thread** conversation state (this chat) | `PostgresSaver` (Lakebase) / `InMemorySaver` |
| **Long-term** | `store` (`BaseStore`) | Durable, **cross-thread** memories about the user | `DatabricksStore` (Managed Agent Memory, Lakebase-backed, semantic) / `InMemoryStore` |

> Short-term = "what did we just say?" (one thread). Long-term = "what do I know
> about this user?" (every thread). LangGraph keeps them as two separate concepts,
> a checkpointer and a store, and so do we.

## How it works (implementation)

This section explains what each piece does under the hood, so you can trust it and
extend it. The whole module is ~3 small files: `short_term.py`, `long_term.py`,
`config.py`.

### The core idea: two standard LangGraph interfaces

LangGraph already has two memory concepts, and this module just supplies a good
implementation of each:

- A **checkpointer** persists the graph's *state for a `thread_id`*. LangGraph
  calls it automatically: it loads the saved state before a run and writes the new
  state after. That is your short-term / conversational memory.
- A **store** (`BaseStore`) is a *key-value store with namespaces and search*. Your
  code reads and writes it explicitly (or the model does, via tools). That is your
  long-term / cross-thread memory.

Because both are the stock interfaces, wiring is one line
(`graph.compile(checkpointer=cp, store=store)`) and the module works in any agent.

### Short-term: `build_checkpointer` (`short_term.py`)

`build_checkpointer(conn, backend="postgres")` returns:

- `PostgresSaver(conn)` for `"postgres"`. It needs an **open psycopg connection**,
  which the app borrows from the Lakebase pool for the lifetime of one request.
- `InMemorySaver()` for `"memory"` (dev/tests; state is lost on restart).

You never call load/save yourself. Once the graph is compiled with the
checkpointer and invoked with `config={"configurable": {"thread_id": ...}}`,
LangGraph:

1. reads the last checkpoint for that `thread_id`,
2. runs the nodes,
3. writes the new checkpoint back.

So the "conversation memory" is just the graph state, persisted per thread. On
Lakebase (Postgres) that state survives restarts and is shared across replicas.

`setup_checkpointer(conn)` runs the one-time table DDL. It flips the connection to
autocommit first, because `CREATE INDEX CONCURRENTLY` can't run inside a
transaction. It's idempotent, so re-running is safe.

### Long-term: `build_store` (`long_term.py`)

`build_store(config)` returns a `BaseStore`, or `None` when long-term is off:

- `"databricks"` → **`DatabricksStore`** (from `databricks-langchain`). It's a
  `BaseStore` backed by **Lakebase**: each operation borrows a connection from a
  pool, opens a short-lived `PostgresStore` on it, runs the op, and returns the
  connection. If you pass `embedding_endpoint` (+ `embedding_dims`,
  `embedding_fields`), it builds a semantic index using `DatabricksEmbeddings`, so
  `search(query=...)` ranks by meaning. Without an embedding endpoint, search
  returns matches by namespace and recency.
- `"memory"` → `InMemoryStore` (dev/tests).
- `"none"` → `None`.

`setup_store(store)` calls `store.setup()` if the backend has one (creates the
store tables). Idempotent and best-effort.

### Namespacing and scope (the isolation model)

Every memory lives under a **namespace tuple** `(*prefix, scope)`, e.g.
`("memories", "a-b-acme-com")`. `scope_for(prefix, scope)` builds it and
`_sanitize(scope)` makes the scope a legal LangGraph label:

```python
def _sanitize(scope):
    # LangGraph labels are non-empty and cannot contain '.', so collapse anything
    # outside [A-Za-z0-9_-] to '-'  (e.g. 'a.b@acme.com' -> 'a-b-acme-com').
    return re.sub(r"[^A-Za-z0-9_-]", "-", (scope or "anon").strip()) or "anon"
```

This is deterministic (same identity, same namespace) and it's why emails work as
scopes. It's also a real bug I hit: without the sanitizer, an email's dot throws
`InvalidNamespaceError` at write time.

The scope is your isolation boundary. Pass a **trusted server-side identity** (the
caller's email/id), never model output, and one user can't read another's memories.

### `recall` and `remember` (the node helpers)

These are deliberately small and defensive:

```python
def recall(store, scope, query, *, prefix=("memories",), limit=5):
    if store is None:                     # long-term disabled -> no-op
        return []
    try:
        items = store.search(scope_for(prefix, scope), query=query, limit=limit)
        return [(it.value or {}).get("memory") or "" for it in items
                if (it.value or {}).get("memory")]
    except Exception:                     # memory must never break a turn
        return []

def remember(store, scope, content, *, prefix=("memories",), key=None):
    if store is None or not content:
        return
    try:
        store.put(scope_for(prefix, scope), key or str(uuid.uuid4()), {"memory": content})
    except Exception:
        pass
```

Two rules on purpose: they **no-op when the store is `None`** (so the exact same
code runs whether or not long-term is enabled), and they **never raise** (a memory
failure degrades to "no memory," it never fails the user's turn). Pass a stable
`key` to `remember` to update a known fact instead of writing a duplicate.

### `make_memory_tools` (for tool-calling agents)

For ReAct-style agents, `make_memory_tools(store, scope_getter)` returns
`save_memory` and `search_memory` tools the model can call. The `scope_getter` is a
zero-arg callable resolved **at call time**, so even here the scope comes from
trusted context, not from the model.

### How a turn actually uses it

```
chat.py:  build_checkpointer(conn)  +  get_store()
          → build_graph(checkpointer=cp, store=store)
          → graph.invoke(msg, config={configurable:{thread_id, caller_email}})

LangGraph injects `store` and `config` into any node that declares them by name.

synthesize(state, config, store):
    scope   = config["configurable"]["caller_email"]      # trusted
    recalled = recall(store, scope, query)                # long-term read
    ... put `recalled` in the prompt, call the LLM ...
    remember(store, scope, "<their taste>")               # long-term write
    # short-term load/save happens automatically via the checkpointer
```

### Startup and graceful degradation (`server/memory_wire.py` in the app)

The long-term store is built **once at app startup** and cached. It's guarded, so a
misconfiguration disables long-term instead of crashing the app:

```python
def init_long_term_memory():
    cfg = MemoryConfig.from_env()
    if not cfg.long_term_enabled:
        set_store(None); return None
    try:
        store = build_store(cfg); setup_store(store); set_store(store)
    except Exception as exc:
        log.warning("long-term memory disabled (init failed): %s", exc)
        set_store(None)
```

### The Lakebase permission gotcha (worth knowing)

`DatabricksStore.setup()` provisions its tables **as the app's service principal**,
in the Lakebase **default database** (`databricks_postgres`), not your app's own
database. The SP needs `CREATE, USAGE ON SCHEMA public` there:

```sql
GRANT USAGE, CREATE ON SCHEMA public TO "<app-sp-client-id>";
```

Miss it and startup logs `permission denied for schema public`, the guard disables
long-term, and the app keeps serving short-term only. Grant it, restart, and the
`store` / `store_migrations` tables appear.

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
