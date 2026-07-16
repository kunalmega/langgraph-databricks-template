# Using the Agent from Outside Databricks — Two Ways

You built the agent so it can be consumed two different ways. Both are **live and
verified**. This doc shows how to call each from any external client (your laptop, a
backend service, another app) — no Databricks notebook required.

| | **Way 1 — FastAPI App** | **Way 2 — Registered Agent Endpoint** |
|---|---|---|
| What it is | The Databricks App (`app.py`) exposing `/api/chat` | The MLflow agent logged via `deploy_agent.py` → Model Serving |
| Conversation memory | ✅ Yes — persisted in **Lakebase** per `thread_id` | ❌ Stateless — you pass full history each call |
| Goes through Unity AI Gateway? | LLM calls do (via `use_ai_gateway=True`); the app itself is not a registered agent | ✅ Yes — first-class **governed agent** on the Agents inventory |
| Governance/versioning/rollback | app-level only | ✅ UC model versions + gateway governance |
| Cost | app compute | scale-to-zero serving endpoint (≈$0 idle) |
| Best for | Interactive UI, chat with memory | Programmatic/governed consumption, other agents calling it |

Both need a **Databricks OAuth token** in the `Authorization: Bearer` header (below).

---

## Prerequisite — get a token (works from anywhere)

**Option A — you have the Databricks CLI (interactive):**
```bash
databricks auth token -p Fevm-fevm-conde | jq -r '.access_token'
```

**Option B — a backend service (machine-to-machine, no browser):**
Create/authorize a service principal with an OAuth secret, then:
```bash
export DATABRICKS_HOST="https://fevm-fevm-cme-conde.cloud.databricks.com"
export CLIENT_ID="<sp-client-id>"
export CLIENT_SECRET="<sp-oauth-secret>"

TOKEN=$(curl -sS -X POST "$DATABRICKS_HOST/oidc/v1/token" \
  -u "$CLIENT_ID:$CLIENT_SECRET" \
  -d 'grant_type=client_credentials' -d 'scope=all-apis' | jq -r '.access_token')
```
Tokens are short-lived (~1h) — mint per session/refresh as needed.

---

## Way 1 — Call the FastAPI App directly (stateful, Lakebase memory)

The app is a normal FastAPI service. It keeps conversation state in Lakebase, so pass a
`thread_id` to continue a conversation.

**Endpoint:** `POST https://langgraph-sample-7474657767854090.aws.databricksapps.com/api/chat`

### curl
```bash
APP_URL="https://langgraph-sample-7474657767854090.aws.databricksapps.com"

# Turn 1 — no thread_id → a new conversation is created and returned
curl -sS -X POST "$APP_URL/api/chat" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"message":"What is 21 * 3? Use the calculator tool."}'
# -> {"reply":"21 * 3 = 63","thread_id":"d655e1c5-..."}

# Turn 2 — pass the SAME thread_id → it REMEMBERS (state from Lakebase)
curl -sS -X POST "$APP_URL/api/chat" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"message":"What did I just ask?","thread_id":"d655e1c5-..."}'
# -> {"reply":"You asked what 21 * 3 is — the answer was 63.","thread_id":"d655e1c5-..."}
```

### Python
```python
import os, requests

APP_URL = "https://langgraph-sample-7474657767854090.aws.databricksapps.com"
HEADERS = {"Authorization": f"Bearer {os.environ['TOKEN']}"}

def chat(message, thread_id=None):
    r = requests.post(f"{APP_URL}/api/chat", headers=HEADERS,
                      json={"message": message, "thread_id": thread_id})
    r.raise_for_status()
    return r.json()

first = chat("What is 21 * 3? Use the calculator tool.")
print(first["reply"])                              # 21 * 3 = 63
follow = chat("What did I just ask?", first["thread_id"])
print(follow["reply"])                             # remembers, via Lakebase
```

**Key point:** memory is server-side. You only carry the `thread_id`; the app reloads
history from Lakebase. This is the path for an interactive chat experience.

---

## Way 2 — Call the registered Agent endpoint (governed, stateless)

This is the agent you logged with `deploy_agent.py`. It's a Databricks Model Serving
endpoint, on the **Unity AI Gateway → Agents** inventory, governed and versioned.

**Endpoint name:** `agents_fevm_cme_conde_catalog-langgraph_demo-langgraph_sample_a`
**URL:** `POST https://fevm-fevm-cme-conde.cloud.databricks.com/serving-endpoints/<endpoint>/invocations`

It's **stateless** — there's no Lakebase thread here; you pass the full `messages` list
each call (standard for a serving endpoint).

### curl
```bash
HOST="https://fevm-fevm-cme-conde.cloud.databricks.com"
EP="agents_fevm_cme_conde_catalog-langgraph_demo-langgraph_sample_a"

curl -sS -X POST "$HOST/serving-endpoints/$EP/invocations" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"What is 21 * 3? Use the calculator tool."}]}'
# -> {"messages":[{"role":"assistant","content":"21 * 3 = 63","id":"..."}], ...}
```

### Multi-turn (you supply the history — stateless)
```bash
curl -sS -X POST "$HOST/serving-endpoints/$EP/invocations" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"messages":[
        {"role":"user","content":"What is 21 * 3?"},
        {"role":"assistant","content":"21 * 3 = 63"},
        {"role":"user","content":"What did I just ask?"}
      ]}'
```

### Python (raw REST)
```python
import os, requests

HOST = "https://fevm-fevm-cme-conde.cloud.databricks.com"
EP   = "agents_fevm_cme_conde_catalog-langgraph_demo-langgraph_sample_a"

def ask(messages):
    r = requests.post(f"{HOST}/serving-endpoints/{EP}/invocations",
                      headers={"Authorization": f"Bearer {os.environ['TOKEN']}"},
                      json={"messages": messages})
    r.raise_for_status()
    return r.json()["messages"][-1]["content"]

print(ask([{"role": "user", "content": "What is 21 * 3? Use the calculator tool."}]))
```

### Python (Databricks SDK for auth, REST for the call)
The SDK is handy for auth (it resolves the token/host), but for a custom ChatAgent the
raw `/invocations` POST returns the cleanest shape:
```python
import requests
from databricks.sdk import WorkspaceClient

w = WorkspaceClient(profile="Fevm-fevm-conde")   # or host+SP env vars off-platform
cfg = w.config
ep  = "agents_fevm_cme_conde_catalog-langgraph_demo-langgraph_sample_a"

r = requests.post(
    f"{cfg.host}/serving-endpoints/{ep}/invocations",
    headers=cfg.authenticate(),                  # adds the Authorization header
    json={"messages": [{"role": "user", "content": "What is 21 * 3? Use the calculator tool."}]},
)
print(r.json()["messages"][-1]["content"])       # 21 * 3 = 63
```

---

## Which one should I use?

- **Interactive app / chat with memory, minimal client code** → **Way 1** (FastAPI + Lakebase).
  The server remembers; you just keep the `thread_id`.
- **Programmatic / governed / other systems or agents calling it, need versioning &
  central inventory** → **Way 2** (registered agent endpoint). You manage conversation
  history yourself, but you get gateway governance, versions, and rollback.

You can run **both at once** — same agent logic (`server/graph.py`), two front doors.
If you want Way 2 to also have durable memory, wire the same `PostgresSaver`/Lakebase
into `agent.py` (currently the endpoint is intentionally stateless).

---

## Notes on running the *caller* outside Databricks

Nothing about the caller must live on Databricks — these are plain HTTPS + Bearer token
calls. From a laptop, a K8s pod, a Lambda, or another cloud:
1. Provide a token (Option B service principal is the norm for services).
2. Ensure network egress to `*.databricksapps.com` (Way 1) and the workspace host (Way 2).
3. For Way 2, the calling identity needs `CAN_QUERY` on the serving endpoint.
