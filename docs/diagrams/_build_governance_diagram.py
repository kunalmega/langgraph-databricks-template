"""Generate the executive 'governed agents on Databricks' architecture diagram.

Run:  python3 docs/diagrams/_build_governance_diagram.py
Produces: docs/diagrams/databricks_agent_governance.drawio
"""
import sys

SKILL = "/Users/kunal.gaurav/.claude/plugins/cache/fe-vibe/fe-specialized-agents/1.0.7/skills/drawio-diagram/scripts"
sys.path.insert(0, SKILL)

from generate_drawio import DrawioBuilder, load_icons  # noqa: E402

icons = load_icons()
b = DrawioBuilder(width=1720, height=1120, icons_cache=icons)

# ---- Title -----------------------------------------------------------------
b.add_title(
    40, 16, 1640,
    "Governed Custom Agents on Databricks",
    subtitle="Every custom agent you build is registered and governed through Unity AI Gateway — agents and model endpoints share one control plane.",
)

# ---- Data-flow banner ------------------------------------------------------
b.add_banner(
    40, 78, 1640,
    "REQUEST FLOW  \u2192  consumers  \u2192  custom agent  \u2192  Unity AI Gateway  \u2192  governed model endpoint  \u2192  usage & audit",
)

TOP = 130

# ---- Column A: Consumers ---------------------------------------------------
cA = b.add_container(40, TOP, 180, 220, "Consumers", "sources")
u_biz = b.add_node(20, 45, 140, 66, "Business user\n(chat UI)", "purple", icon_name="human", parent=cA)
u_app = b.add_node(20, 125, 140, 66, "Other apps\n(programmatic)", "purple", icon_name="computer", parent=cA)

# ---- Column B: Custom agent (one definition, two surfaces) -----------------
cB = b.add_container(250, TOP, 280, 400, "Custom agent  \u2014  one definition", "ingestion")
app_api = b.add_node(
    20, 45, 240, 72,
    "Databricks App API\nPOST /api/chat  \u00b7  runs the agent\n+ Lakebase memory",
    "blue", icon_name="apps_services", parent=cB,
)
agent_core = b.add_node(
    20, 140, 240, 72,
    "Custom LangGraph agent\nserver/graph.py  \u00b7  tools + logic",
    "green", icon_name="langchain", parent=cB,
)
deploy = b.add_node(
    20, 235, 240, 66,
    "Register & deploy\ndeploy_agent.py  \u2192  Unity Catalog",
    "indigo", icon_name="mlflow", parent=cB,
)
agent_ep = b.add_node(
    20, 320, 240, 66,
    "Governed Agent endpoint\nAgents inventory on AI Gateway",
    "teal", icon_name="endpoint", parent=cB,
)

# ---- Column C: Unity AI Gateway (control plane for agents + models) --------
cC = b.add_container(570, TOP, 280, 400, "Unity AI Gateway  \u2014  single control plane", "governance")
gw_agents = b.add_node(
    20, 45, 240, 72,
    "Agent governance\nACLs  \u00b7  versioning  \u00b7  guardrails\nall custom agents registered here",
    "gov", icon_name="unity_catalog", parent=cC,
)
gw_models = b.add_node(
    20, 140, 240, 72,
    "Model endpoint governance\nrate limits  \u00b7  guardrails\nmodel endpoints live here",
    "gov", icon_name="ai", parent=cC,
)
gw_meter = b.add_node(
    20, 235, 240, 72,
    "Per-agent metering\nrequester identity + request_tags\n{agent, app}",
    "gov", icon_name="data_security", parent=cC,
)
gw_route = b.add_node(
    20, 330, 240, 66,
    "Every LLM call routed\nthrough the Gateway",
    "gov", icon_name="endpoint", parent=cC,
)

# ---- Column D: Governed model endpoint (inside Gateway) --------------------
cD = b.add_container(890, TOP, 220, 220, "Governed model endpoint", "aiml")
model = b.add_node(
    20, 45, 180, 80,
    "Foundation model endpoint\n(e.g. claude-sonnet-5)\nlives in Unity AI Gateway",
    "orange", icon_name="serverless", parent=cD,
)
model_note = b.add_node(
    20, 140, 180, 66,
    "Shared safely by\nany governed agent",
    "yellow", icon_name="ai", parent=cD,
)

# ---- Column E: State + governance insights ---------------------------------
cE = b.add_container(1150, TOP, 250, 400, "State & governance insights", "consumption")
lake = b.add_node(
    20, 45, 210, 66,
    "Lakebase (Postgres)\nconversation memory\nper thread_id",
    "yellow", icon_name="postgresql", parent=cE,
)
usage = b.add_node(
    20, 130, 210, 78,
    "system.ai_gateway.usage\ntokens  \u00b7  latency  \u00b7  cost\nper agent via request_tags",
    "silver", icon_name="catalog_store", parent=cE,
)
dash = b.add_node(
    20, 230, 210, 66,
    "AI/BI dashboard\ncost & usage per agent",
    "green", icon_name="data_warehouse", parent=cE,
)

b.add_edge(model, model_note, "", exit_x=0.5, exit_y=1, entry_x=0.5, entry_y=0, dashed=True)
b.add_edge(u_biz, app_api, "", exit_x=1, exit_y=0.5, entry_x=0, entry_y=0.3, stroke_width=2.0)
b.add_edge(u_app, agent_ep, "", exit_x=1, exit_y=0.5, entry_x=0, entry_y=0.5, stroke_width=2.0)
b.add_edge(u_app, app_api, "", exit_x=1, exit_y=0.3, entry_x=0, entry_y=0.7, dashed=True)

# App API runs the custom agent
b.add_edge(app_api, agent_core, "runs", exit_x=0.5, exit_y=1, entry_x=0.5, entry_y=0, stroke_width=2.0)

# Registration path: custom agent -> deploy -> governed endpoint
b.add_edge(agent_core, deploy, "package & register", exit_x=0.5, exit_y=1, entry_x=0.5, entry_y=0,
           dashed=True, stroke_color="#3F51B5")
b.add_edge(deploy, agent_ep, "deploy", exit_x=0.5, exit_y=1, entry_x=0.5, entry_y=0,
           dashed=True, stroke_color="#3F51B5")

# Both surfaces route LLM calls through Gateway
b.add_edge(agent_core, gw_route, "LLM call (tagged)", exit_x=1, exit_y=0.5, entry_x=0, entry_y=0.5,
           stroke_width=2.5, font_style=1, stroke_color="#B71C1C")
b.add_edge(agent_ep, gw_agents, "invocations", exit_x=1, exit_y=0.5, entry_x=0, entry_y=0.3,
           stroke_width=2.5, font_style=1, stroke_color="#B71C1C")
b.add_edge(gw_agents, gw_route, "", exit_x=0.5, exit_y=1, entry_x=0.5, entry_y=0, dashed=True)

# Gateway routes to governed model endpoint
b.add_edge(gw_route, model, "governed route", exit_x=1, exit_y=0.5, entry_x=0, entry_y=0.5,
           stroke_width=2.5, font_style=1, stroke_color="#B71C1C")
b.add_edge(gw_models, model, "", exit_x=1, exit_y=0.5, entry_x=0, entry_y=0.8, dashed=True)

# Metering logs
b.add_edge(gw_meter, usage, "logs every call", exit_x=1, exit_y=0.5, entry_x=0, entry_y=0.5, dashed=True)
b.add_edge(usage, dash, "", exit_x=0.5, exit_y=1, entry_x=0.5, entry_y=0, dashed=True)

# App memory
b.add_edge(app_api, lake, "save / load", exit_x=1, exit_y=0.2, entry_x=0, entry_y=0.5,
           dashed=True, stroke_color="#F57F17")

# ---- Persuasive callout ----------------------------------------------------
b.add_note(
    570, 550, 540, 88,
    "ALL CUSTOM AGENTS ARE GOVERNED THROUGH UNITY AI GATEWAY.\n"
    "Build once in LangGraph, run via the App API or register as a governed Agent endpoint. "
    "Model endpoints also live in the Gateway \u2014 one control plane for agents, models, "
    "access, guardrails, and per-agent usage tracking.",
)

# ---- Governance banner -----------------------------------------------------
gov = b.add_container(
    40, 660, 1360, 130,
    "Governance is built in \u2014 not bolted on (one control plane for every custom agent and model)",
    "governance", dashed=True,
)
b.add_node(20, 48, 150, 66, "Identity\nSP per app", "gov", icon_name="identity", parent=gov)
b.add_node(190, 48, 150, 66, "Access control\nACLs", "gov", icon_name="enterprise_security", parent=gov)
b.add_node(360, 48, 150, 66, "Guardrails\nPII / safety", "gov", icon_name="data_security", parent=gov)
b.add_node(530, 48, 160, 66, "Rate limits\n& quotas", "gov", icon_name="compliance", parent=gov)
b.add_node(710, 48, 170, 66, "Usage per agent\nrequest_tags", "gov", icon_name="catalog_store", parent=gov)
b.add_node(900, 48, 170, 66, "Lineage & audit\nUnity Catalog", "gov", icon_name="unity_catalog", parent=gov)
b.add_node(1090, 48, 170, 66, "Versioning\nUC registry", "gov", icon_name="model_registry", parent=gov)

# ---- Numbered walkthrough --------------------------------------------------
b.add_step_number(400, TOP + 50, 1)
b.add_step_number(710, TOP + 50, 2)
b.add_step_number(1000, TOP + 30, 3)
b.add_step_number(1270, TOP + 40, 4)

b.add_flow_description(40, 810, 1360, [
    {
        "num": 1,
        "text": "You build a custom LangGraph agent once (server/graph.py). The Databricks App API runs it with Lakebase memory; the same agent is packaged and registered as a governed Agent endpoint on Unity AI Gateway.",
    },
    {
        "num": 2,
        "text": "Unity AI Gateway is the single control plane for all custom agents and model endpoints \u2014 ACLs, guardrails, rate limits, and versioning apply to every agent you create.",
    },
    {
        "num": 3,
        "text": "Every LLM call from any agent is routed through the Gateway to a governed model endpoint that also lives in Unity AI Gateway. Multiple agents can safely share the same model.",
    },
    {
        "num": 4,
        "text": "Usage is metered per agent via request_tags and service-principal identity. Query system.ai_gateway.usage for tokens, cost, and latency per agent; the App persists conversation memory in Lakebase.",
    },
], title="How a request flows")

b.print_validation()
out = "/Users/kunal.gaurav/Documents/vibe/langgraph template app/docs/diagrams/databricks_agent_governance.drawio"
b.save(out)
print("SAVED", out)
