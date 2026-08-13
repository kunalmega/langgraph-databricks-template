# Register an externally-hosted agent into Unity Catalog

Make an agent that runs **outside Databricks** (on EKS, AKS, GKE, or any HTTP
endpoint) show up on the **Unity AI Gateway → Agents** inventory, so it's
governed and permissioned alongside your Databricks-native agents.

This folder is **standalone** — it does not touch the rest of the template.

## How it works — two-part registration

1. **Unity Catalog HTTP connection** — securely holds the agent's base URL +
   bearer token (the credential holder). Created by the script.
2. **Agent service (type `EXTERNAL`)** — the governed entity that references the
   connection and describes the agent (`base_path`, `system_prompt`). This is
   what appears in the inventory.

```
your agent on EKS/AKS  ──referenced by──►  UC HTTP connection  ──used by──►  Agent service (EXTERNAL)
  (https://.../v1/chat)                    (host + bearer token)             → AI Gateway Agents inventory
```

## Run it

```bash
cp external-agents/.env.example external-agents/.env    # then edit
set -a; source external-agents/.env; set +a
bash external-agents/register_external_agent.sh
```

## ⚠️ Beta limitation (read this)

Per the [Databricks docs](https://docs.databricks.com/aws/en/ai-gateway/agent-services),
this feature is in **beta**:

- ✅ **Registration + permissions work** — the agent appears in the inventory and
  can be governed (permissions, lineage).
- ❌ **Runtime invocation is NOT available yet** — you **cannot call** the external
  agent *through* the registered agent service. Consumers still call the external
  endpoint directly for now.

So today this gives you **visibility and governance**, not a Databricks-proxied
call path. That's a platform limitation, not a script issue.

## Verify

After running, check **Serving → Unity AI Gateway → Agents** for
`<UC_CATALOG>.<UC_SCHEMA>.<AGENT_SERVICE_ID>`, or:

```bash
databricks api get "/api/2.1/unity-catalog/agent-services/<catalog>.<schema>.<agent_service_id>" -p <profile>
```

## Files

| File | What it is |
|---|---|
| `register_external_agent.sh` | Creates the UC HTTP connection, then registers the external agent service. |
| `.env.example` | Copy to `.env` — all config (workspace, UC target, agent URL, token). |
