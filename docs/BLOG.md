# A LangGraph concierge on Databricks: the architecture, and how we gave it memory

This is an engineering write-up of a small but real agent: a mood-based Indian
cuisine concierge. You tell it how you're feeling (and optionally your city), and
it suggests a dish with a recipe. It runs as a Databricks App, keeps its state in
Lakebase, and routes every model call through Unity AI Gateway.

The focus here is two things: how the agent works, and how we gave it memory. The
memory part is the one worth your time.

---

## The shape of the agent

The agent is a LangGraph `StateGraph` with explicit nodes, so the routing is
visible in `server/graph.py`:

```
START → analyze_mood → [greet] / [pair_weather] → find_dishes → [get_recipe] → synthesize → END
```

`analyze_mood` reads the message and picks an intent. A plain "hi" routes to a
short `greet` reply instead of forcing a dish on someone who just said hello. A
city routes through `pair_weather`, which checks live weather. The nodes that
reason call the LLM; the ones that fetch data call plain HTTP tools.

One design choice pays off later. `build_graph()` takes an optional `checkpointer`
and `store`, so the same graph runs two ways (a stateful web app, and a stateless
serving endpoint) with no changes to the graph itself.

Every model call goes through `build_llm()`, which returns
`ChatDatabricks(model=..., use_ai_gateway=True)`. Governance is on from the first
call, and swapping the model is one config value.

---

## The LLM: a governed model service with a traffic split

The app routes through a Unity AI Gateway **model service** rather than calling a
foundation model directly. A model service is a Unity Catalog object that fronts
one or more models with governance in front of them.

Ours splits traffic 80/20 between Claude Sonnet and GPT, with a third model as a
fallback on failure:

```json
"routing": {
  "destinations": [
    {"name": "primary",    "traffic_percentage": 80,
     "pay_per_token_config": {"model": "models/system.ai.databricks-claude-sonnet-5"}},
    {"name": "challenger", "traffic_percentage": 20,
     "pay_per_token_config": {"model": "models/system.ai.databricks-gpt-5-5"}}
  ],
  "traffic_splitting": {},
  "fallback": {"destinations": [
    {"name": "backup",
     "pay_per_token_config": {"model": "models/system.ai.databricks-claude-sonnet-4-5"}}
  ]}
}
```

The app points `MODEL_SERVICE` at that object and calls it as itself (OAuth), so
there's no token to manage. Want to A/B a new model? Nudge the percentages. No
redeploy.

---

## The UI

The front door is a landing page that explains what the concierge does, with a
Google-style search bar. Ask a question and the page switches to a full-page chat
(the whole page, not a corner popup). Replies render as markdown. It's one
self-contained HTML file, no build step, so npm never enters the picture.

That's the functionality. Now the part I actually want to talk about.

---

## Memory: two kinds, two interfaces

Agents need two different kinds of memory, and it helps to keep them apart.

**Short-term** memory answers "what did we just say?" It's scoped to one
conversation (one thread). In LangGraph that's a **checkpointer**.

**Long-term** memory answers "what do I know about this person?" It spans every
conversation they've ever had. In LangGraph that's a **store**.

LangGraph keeps these as two separate objects, and so do we. The payoff: both are
standard LangGraph interfaces, so the memory code drops into any LangGraph agent.

### Short-term: a checkpointer on Lakebase

The factory is tiny (`memory/short_term.py`):

```python
def build_checkpointer(conn=None, backend="postgres"):
    if backend == "memory":
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver()
    if backend == "postgres":
        if conn is None:
            raise ValueError("backend='postgres' requires an open psycopg conn ...")
        from langgraph.checkpoint.postgres import PostgresSaver
        return PostgresSaver(conn)
```

Each turn borrows a Lakebase connection and hands it to the checkpointer
(`server/routes/chat.py`):

```python
with get_pool().connection() as conn:
    checkpointer = build_checkpointer(conn)
    graph = build_graph(checkpointer=checkpointer, store=get_store())
    result = graph.invoke(
        {"messages": [{"role": "user", "content": req.message}]},
        config={"configurable": {
            "thread_id": f"{owner}:{thread_id}",
            "caller_user": caller.user,
            "caller_email": caller.email,     # the trusted long-term scope
        }},
    )
```

LangGraph loads the prior turns for that `thread_id`, runs the graph, and saves the
new turn back. The state lives in Lakebase (Postgres), so it survives restarts and
works across replicas. Note the same call passes `store=get_store()` for the
long-term tier, and stamps the caller's identity into `configurable`.

### Long-term: a store, backed by Databricks Managed Agent Memory

For long-term we use `DatabricksStore` from `databricks-langchain`. It implements
LangGraph's `BaseStore` over Databricks Managed Agent Memory, stored in Lakebase,
with optional semantic search through a Databricks embeddings endpoint.

Memories are namespaced by a scope you control, usually the user:

```python
store.put(("memories", "a-b-acme-com"), key, {"memory": "prefers spicy paneer"})
store.search(("memories", "a-b-acme-com"), query="what do they like?")
```

Because it's a `BaseStore`, wiring it in is one argument:
`graph.compile(checkpointer=cp, store=store)`, or
`create_react_agent(llm, tools, checkpointer=cp, store=store)`. Nodes that declare
a `store` parameter get it injected by name.

### The modular part

All of this lives in a self-contained `memory/` folder you can copy into any
LangGraph project:

- `build_checkpointer(conn)` for the short-term tier.
- `build_store(config)` for the long-term tier (Databricks, in-memory, or off).
- `recall(...)` and `remember(...)` for custom graphs.
- `make_memory_tools(...)` for tool-calling agents that let the model save and search.
- `MemoryConfig`, env-driven and fully overridable.

Nothing in it depends on this app. Point it at your Lakebase, set
`MEMORY_LONG_TERM=databricks`, and it works. That's the whole point: lift the
folder out, drop it into the next agent, and both tiers come along.

The `recall` and `remember` helpers are deliberately boring (`memory/long_term.py`).
They no-op when the store is off and swallow errors, so memory never breaks a turn:

```python
def recall(store, scope, query, *, prefix=("memories",), limit=5):
    if store is None:
        return []
    try:
        items = store.search(scope_for(prefix, scope), query=query, limit=limit)
        return [(it.value or {}).get("memory") or "" for it in items
                if (it.value or {}).get("memory")]
    except Exception:
        return []

def remember(store, scope, content, *, prefix=("memories",), key=None):
    if store is None or not content:
        return
    try:
        store.put(scope_for(prefix, scope), key or str(uuid.uuid4()), {"memory": content})
    except Exception:
        pass
```

`scope_for(prefix, scope)` builds the namespace and sanitizes the scope into a valid
LangGraph label. That sanitizer is where a real bug lived: LangGraph namespace
labels can't contain a period, so an email like `a.b@acme.com` blew up until we
collapsed it to `a-b-acme-com`.

### How the concierge uses it

In `synthesize`, if a store is wired in, the agent recalls a few things it knows
about the guest, folds them into the prompt, and after it answers, remembers their
taste. LangGraph injects `store` by parameter name, so the node just declares it
(`server/graph.py`):

```python
def synthesize(state, config=None, store=None):
    context = _format_context(state)
    scope = _memory_scope(config)                 # caller_email, from trusted config

    if store is not None:                          # recall, and personalize
        from memory import recall
        query = state.get("mood_summary") or _latest_user_text(state)
        remembered = recall(store, scope, query, limit=3)
        if remembered:
            context = ("What we remember about this guest:\n"
                       + "\n".join(f"- {m}" for m in remembered) + "\n\n" + context)

    reply = build_llm().invoke([("system", SYNTH_PROMPT), ("human", f"Context:\n{context}")])
    content = getattr(reply, "content", None) or _deterministic_reply(state)

    if store is not None:                          # remember their taste for next time
        from memory import remember
        flavor, keyword = state.get("flavor_profile"), state.get("search_keyword")
        if flavor or keyword:
            remember(store, scope, f"Enjoys {flavor or 'flavorful'} dishes such as {keyword or 'curry'}.")

    return {"messages": [AIMessage(content=content)]}
```

So a fresh conversation can open with "since you usually go for spicy paneer," even
though it's a different thread from where they said it.

If no store is configured, both blocks are skipped and nothing imports the memory
package. The same code runs everywhere, including the stateless serving endpoint
where `store` is `None`.

### Best practices we followed

- **Scope comes from a trusted server-side identity** (the caller's email), never
  from model output. The model can't choose whose memories it reads.
- **`recall` and `remember` never raise.** Memory shouldn't break a turn, so a
  failure degrades to "no memory," gets logged, and the turn continues.
- **Long-term is off by default.** With `MEMORY_LONG_TERM` unset, the app behaves
  as a short-term-only app, so you opt in per deployment.

### A gotcha worth sharing

When we turned long-term memory on in a real workspace, startup logged:
`permission denied for schema public`.

Here's what was happening. `DatabricksStore` provisions its own tables, and it does
that as the app's **service principal**, in the Lakebase default database
(`databricks_postgres`), not the app's own database. The service principal had
rights on the app database but nothing on `public` in the default one. Granting it
`CREATE, USAGE ON SCHEMA public` there fixed it, and the store tables appeared on
the next restart.

The guard earned its keep in the meantime. The app kept serving with long-term
quietly disabled instead of crashing.

---

## The two tiers, one store

```mermaid
flowchart LR
    turn([A user turn]):::ext --> graph["LangGraph agent<br/>server/graph.py"]:::agent

    graph -->|"per-thread state<br/>(load + save)"| cp["Short-term<br/>checkpointer · PostgresSaver"]:::st
    graph -->|"recall / remember<br/>(per user)"| store["Long-term<br/>store · DatabricksStore"]:::lt

    cp --> lake[("💾 Lakebase<br/>Postgres")]:::store
    store --> lake
    store -.->|"optional semantic search"| emb["Embeddings endpoint"]:::model

    classDef ext fill:#e8eaf6,stroke:#5c6bc0,color:#1a237e;
    classDef agent fill:#fff3e0,stroke:#fb8c00,color:#e65100;
    classDef st fill:#e3f2fd,stroke:#1976d2,color:#0d47a1;
    classDef lt fill:#fce4ec,stroke:#e91e63,color:#880e4f;
    classDef store fill:#e8f5e9,stroke:#43a047,color:#1b5e20;
    classDef model fill:#f3e5f5,stroke:#8e24aa,color:#4a148c;
```

Short-term is the conversation. Long-term is the person. Both sit on the same
Lakebase, both are plain LangGraph interfaces, and the `memory/` folder is the
piece you reuse.

---

## Files to read

| Area | File | What's in it |
|---|---|---|
| The agent | `server/graph.py` | The `StateGraph`, `build_llm()`, and the memory recall/remember in `synthesize`. |
| Short-term | `memory/short_term.py` | `build_checkpointer()` (PostgresSaver / in-memory). |
| Long-term | `memory/long_term.py` | `build_store()`, `recall`/`remember`, `make_memory_tools()`. |
| Config | `memory/config.py` | `MemoryConfig`, env-driven. |
| Wiring | `app.py`, `server/routes/chat.py` | Store built once at startup; each turn passes checkpointer + store to the graph. |
| Governed LLM | the model service (Unity AI Gateway) | 80/20 split + fallback, reached via `MODEL_SERVICE`. |

Start with `memory/README.md` for the drop-in guide, and `docs/ARCHITECTURE.md`
for how the rest fits together.
