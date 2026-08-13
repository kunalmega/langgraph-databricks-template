"""The LangGraph agent: a multi-node "Mood-based Indian Cuisine Concierge".

You tell it your mood/craving (optionally a city); it classifies the mood,
optionally factors in live weather, finds matching Indian dishes, pulls a real
recipe, and writes a warm recommendation. An explicit StateGraph — not a flat
ReAct loop — so the routing is visible:

    START -> analyze_mood -> [pair_weather] -> find_dishes -> [get_recipe]
          -> synthesize -> END
    (pair_weather runs only when a city is given; get_recipe only when a dish
    is found — otherwise those steps are skipped straight to synthesize.)

The same graph is reused two ways (contract preserved):
  1. Behind the FastAPI app, checkpointed to Lakebase for durable memory.
  2. Wrapped as an MLflow ChatAgent (agent.py), stateless.
Both call build_graph(checkpointer=...), invoke with {"messages":[...]}, and
read the reply at output["messages"][-1].content.

Only analyze_mood + synthesize call the LLM (routed through Unity AI Gateway via
build_llm()); the dish/recipe/weather tools are plain HTTP (server/tools/).
"""
import json
import logging
import os
from typing import Literal, Optional

import mlflow
from databricks_langchain import ChatDatabricks
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from pydantic import BaseModel, Field

from .config import get_serving_endpoint
from .tools import get_weather, lookup_meal, search_meals

logger = logging.getLogger("graph")

# Trace LangChain/LangGraph calls into MLflow.
mlflow.langchain.autolog()


# --- LLM ----------------------------------------------------------------------

# Name this app/agent in the Unity AI Gateway usage view via request tags. These
# populate the request_tags column in system.ai_gateway.usage so all custom apps can
# be attributed/filtered by agent in one place.
AGENT_NAME = os.environ.get("AGENT_NAME", "langgraph-sample-agent")
APP_NAME = os.environ.get("DATABRICKS_APP_NAME", "langgraph-sample")


# A Unity AI Gateway REQUEST TAG that names this agent/app in system.ai_gateway.usage.
_REQUEST_TAGS = '{"agent":"%s","app":"%s"}' % (AGENT_NAME, APP_NAME)

# Optional: a Unity AI Gateway "model service" (a UC object catalog.schema.name that
# fronts a primary + fallback model with governance). Set MODEL_SERVICE to its fully
# qualified name to route through it instead of a plain endpoint.
MODEL_SERVICE = os.environ.get("MODEL_SERVICE")


def build_llm():
    """Chat model routed through Unity AI Gateway. Two modes:

    1. MODEL_SERVICE set  -> route to a Unity AI Gateway *model service*
       (catalog.schema.name, e.g. fevm_cme_conde_catalog.langgraph_demo.sonnet4) via the
       native gateway URL https://<host>/ai-gateway/mlflow/v1. This is the governed,
       fallback-protected path (primary + fallback configured on the model service).
    2. otherwise           -> ChatDatabricks(use_ai_gateway=True) against UAIG_ENDPOINT.

    Both attach request tags so the agent is attributable in the gateway usage view.
    Note: some models (e.g. claude-sonnet-5 reasoning) reject temperature, so we omit it.
    """
    tags_header = {"Databricks-Ai-Gateway-Request-Tags": _REQUEST_TAGS}

    if MODEL_SERVICE:
        from langchain_openai import ChatOpenAI

        from .config import get_oauth_token, get_workspace_host
        from .settings import get_settings

        # Auth for the model service is an EXPLICIT identity choice, so authorization
        # behavior is predictable across app and local execution (MODEL_SERVICE_AUTH):
        #   "token" (default) -> require MODEL_SERVICE_TOKEN (a PAT/secret with EXECUTE
        #                        on the model service). We do NOT silently fall back to
        #                        the caller's OAuth identity.
        #   "oauth"           -> deliberately call as the current app/user identity.
        auth_mode = get_settings().model_service_auth
        explicit_token = os.environ.get("MODEL_SERVICE_TOKEN")
        if auth_mode == "oauth":
            token = get_oauth_token()
        elif explicit_token:
            token = explicit_token
        else:
            raise RuntimeError(
                "MODEL_SERVICE is set but MODEL_SERVICE_TOKEN is missing. Provide the "
                "token (ideally a Databricks secret), or set MODEL_SERVICE_AUTH=oauth "
                "to deliberately call the model service as the current identity."
            )
        base_url = f"{get_workspace_host().rstrip('/')}/ai-gateway/mlflow/v1"
        return ChatOpenAI(
            model=MODEL_SERVICE,
            base_url=base_url,
            api_key=token,
            max_tokens=1024,
            default_headers=tags_header,
        )

    return ChatDatabricks(
        model=get_serving_endpoint(),
        use_ai_gateway=True,
        max_tokens=1024,
        extra_params={"extra_headers": tags_header},
    )


# --- Graph state --------------------------------------------------------------


class CuisineState(MessagesState):
    """Conversation state. Extends MessagesState (adds the `messages` list +
    add-reducer + checkpointing). Every field below is optional and populated by
    nodes — nothing is seeded on input, so callers stay unchanged."""

    intent: Optional[str]           # "suggest" | "recipe" | "weather_pairing"
    flavor_profile: Optional[str]   # e.g. "spicy comfort"
    search_keyword: Optional[str]   # TheMealDB search term
    mood_summary: Optional[str]     # short human phrase for the synth prompt
    location: Optional[str]         # city for weather pairing
    candidates: Optional[list]      # [{id,name,thumb,category,area}]
    chosen_id: Optional[str]        # meal id to look up
    recipe: Optional[dict]          # full normalized recipe
    weather: Optional[dict]         # {location,temp_c,condition,nudge,...}
    errors: Optional[list]          # non-fatal issues surfaced to synthesize


# --- Prompts ------------------------------------------------------------------

MOOD_PROMPT = (
    "You are the mood analyst for an Indian-cuisine concierge. Read the user's "
    "message and classify it. Choose a `search_keyword` that is a real Indian "
    "dish or ingredient likely to match a recipe database (e.g. 'curry', "
    "'biryani', 'paneer', 'masala', 'tandoori', 'dal'). "
    "Set intent to 'weather_pairing' ONLY if the user names a city/place and the "
    "weather could matter; 'recipe' if they explicitly want a recipe or how-to; "
    "otherwise 'suggest'. Extract a city into `location` if one is mentioned."
)

SYNTH_PROMPT = (
    "You are a warm, upbeat Indian-cuisine concierge. Using the structured "
    "context provided, recommend ONE dish enthusiastically and helpfully. "
    "Include: the dish name, one line on why it fits their mood/weather, 3-5 key "
    "ingredients, a one-sentence teaser of the method, the YouTube link if "
    "present, and 1-2 alternative dishes to consider. Keep it friendly and "
    "concise. If information is missing, be graceful and still helpful."
)


# --- Structured output schema for mood analysis -------------------------------


class MoodAnalysis(BaseModel):
    intent: Literal["suggest", "recipe", "weather_pairing"] = "suggest"
    flavor_profile: str = Field(default="comforting", description="short flavor mood")
    search_keyword: str = Field(default="curry", description="an Indian dish/ingredient")
    mood_summary: str = Field(default="", description="one short phrase about the mood")
    location: Optional[str] = Field(default=None, description="a city, if mentioned")


_HEURISTIC = [
    (("rain", "cold", "cozy", "sad", "tired", "comfort", "winter"), "curry", "cozy comfort"),
    (("celebrate", "party", "festive", "feast", "happy", "special"), "biryani", "festive"),
    (("light", "healthy", "fresh", "hot", "summer", "diet"), "tandoori", "light and fresh"),
]


def _latest_user_text(state: CuisineState) -> str:
    for msg in reversed(state["messages"]):
        # Works for both dict-form and LangChain message objects.
        role = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role in ("human", "user"):
            return getattr(msg, "content", None) or msg.get("content", "")
    # Fall back to the last message content.
    last = state["messages"][-1]
    return getattr(last, "content", None) or (last.get("content", "") if isinstance(last, dict) else "")


# --- Nodes --------------------------------------------------------------------


def analyze_mood(state: CuisineState) -> dict:
    """LLM node: classify mood -> intent + flavor + dish keyword (+ city).

    Three-tier fallback so it never raises: structured output -> JSON parse ->
    keyword heuristic.
    """
    text = _latest_user_text(state)

    # Tier 1: structured output.
    try:
        llm = build_llm().with_structured_output(MoodAnalysis)
        result: MoodAnalysis = llm.invoke(
            [("system", MOOD_PROMPT), ("human", text)]
        )
        return _mood_to_state(result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("analyze_mood structured output failed: %s", exc)

    # Tier 2: plain LLM, parse JSON.
    try:
        raw = build_llm().invoke(
            [("system", MOOD_PROMPT + " Reply ONLY with compact JSON matching keys: "
              "intent, flavor_profile, search_keyword, mood_summary, location."),
             ("human", text)]
        )
        content = getattr(raw, "content", raw)
        parsed = MoodAnalysis(**json.loads(content))
        return _mood_to_state(parsed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("analyze_mood JSON fallback failed: %s", exc)

    # Tier 3: keyword heuristic.
    low = text.lower()
    keyword, flavor = "chicken", "something tasty"
    for triggers, kw, fl in _HEURISTIC:
        if any(t in low for t in triggers):
            keyword, flavor = kw, fl
            break
    return {
        "intent": "suggest",
        "flavor_profile": flavor,
        "search_keyword": keyword,
        "mood_summary": text[:120],
        "location": None,
    }


def _mood_to_state(m: MoodAnalysis) -> dict:
    return {
        "intent": m.intent,
        "flavor_profile": m.flavor_profile,
        "search_keyword": m.search_keyword,
        "mood_summary": m.mood_summary or "",
        "location": m.location,
    }


def pair_weather(state: CuisineState) -> dict:
    """HTTP node: fetch weather for the city and nudge the dish keyword."""
    city = state.get("location")
    if not city:
        return {}
    weather = get_weather(city)
    if not weather:
        return {"errors": (state.get("errors") or []) + [f"weather unavailable for {city}"]}
    out: dict = {"weather": weather}
    override = weather.get("keyword_override")
    if override:
        out["search_keyword"] = override
    return out


_FALLBACK_KEYWORDS = ["curry", "masala", "biryani", "paneer", "chicken"]


def find_dishes(state: CuisineState) -> dict:
    """HTTP node: search Indian dishes; retry across common keywords if empty."""
    keyword = state.get("search_keyword") or "curry"
    candidates = search_meals(keyword)
    if not candidates:
        for kw in _FALLBACK_KEYWORDS:
            candidates = search_meals(kw)
            if candidates:
                break
    if not candidates:
        return {"candidates": [], "errors": (state.get("errors") or []) + ["no dishes found"]}
    return {"candidates": candidates, "chosen_id": candidates[0]["id"]}


def get_recipe(state: CuisineState) -> dict:
    """HTTP node: fetch the full recipe for the chosen dish."""
    chosen = state.get("chosen_id")
    if not chosen:
        return {}
    recipe = lookup_meal(chosen)
    if not recipe:
        return {"errors": (state.get("errors") or []) + ["recipe lookup failed"]}
    return {"recipe": recipe}


def _format_context(state: CuisineState) -> str:
    """Compact, LLM-friendly summary of what the tools gathered."""
    parts = [
        f"Mood: {state.get('mood_summary') or 'n/a'}",
        f"Flavor profile: {state.get('flavor_profile') or 'n/a'}",
    ]
    weather = state.get("weather")
    if weather:
        parts.append(
            f"Weather in {weather['location']}: {weather['temp_c']}°C, "
            f"{weather['condition']} — {weather['nudge']}"
        )
    recipe = state.get("recipe")
    if recipe:
        ings = ", ".join(f"{item} ({m})" if m else item
                         for item, m in recipe["ingredients"][:8])
        parts.append(f"Recommended dish: {recipe['name']} ({recipe.get('area','')})")
        parts.append(f"Ingredients: {ings}")
        parts.append(f"Instructions: {recipe['instructions'][:600]}")
        if recipe.get("youtube"):
            parts.append(f"YouTube: {recipe['youtube']}")
    cands = state.get("candidates") or []
    alts = [c["name"] for c in cands if not recipe or c["id"] != recipe.get("id")][:3]
    if alts:
        parts.append("Alternatives: " + ", ".join(alts))
    errors = state.get("errors")
    if errors:
        parts.append("Note (be graceful about these): " + "; ".join(errors))
    return "\n".join(parts)


def _deterministic_reply(state: CuisineState) -> str:
    """Fallback reply if the LLM synth call fails — still useful."""
    recipe = state.get("recipe")
    if recipe:
        ings = ", ".join(item for item, _ in recipe["ingredients"][:5])
        msg = (f"How about **{recipe['name']}**? "
               f"Key ingredients: {ings}. ")
        if recipe.get("youtube"):
            msg += f"Watch it here: {recipe['youtube']}"
        return msg.strip()
    cands = state.get("candidates") or []
    if cands:
        return "You might enjoy: " + ", ".join(c["name"] for c in cands[:3]) + "."
    return ("I couldn't reach the recipe service just now, but tell me your mood "
            "and I'll suggest an Indian dish as soon as it's back!")


def synthesize(state: CuisineState) -> dict:
    """Terminal LLM node: compose the final warm recommendation.

    Always appends exactly one AIMessage so callers can read
    output['messages'][-1].content.
    """
    context = _format_context(state)
    try:
        reply = build_llm().invoke(
            [("system", SYNTH_PROMPT), ("human", f"Context:\n{context}")]
        )
        content = getattr(reply, "content", None) or _deterministic_reply(state)
    except Exception as exc:  # noqa: BLE001
        logger.warning("synthesize LLM failed, using deterministic reply: %s", exc)
        content = _deterministic_reply(state)
    return {"messages": [AIMessage(content=content)]}


# --- Routing ------------------------------------------------------------------


def route_after_mood(state: CuisineState) -> str:
    if state.get("intent") == "weather_pairing" and state.get("location"):
        return "pair_weather"
    return "find_dishes"


def route_after_dishes(state: CuisineState) -> str:
    return "synthesize" if not state.get("candidates") else "get_recipe"


# --- Graph --------------------------------------------------------------------


def build_graph(checkpointer=None):
    """Create the cuisine-concierge graph, optionally with a checkpointer for
    durable state. Signature/contract unchanged so both callers keep working."""
    g = StateGraph(CuisineState)
    g.add_node("analyze_mood", analyze_mood)
    g.add_node("pair_weather", pair_weather)
    g.add_node("find_dishes", find_dishes)
    g.add_node("get_recipe", get_recipe)
    g.add_node("synthesize", synthesize)

    g.add_edge(START, "analyze_mood")
    g.add_conditional_edges(
        "analyze_mood", route_after_mood,
        {"pair_weather": "pair_weather", "find_dishes": "find_dishes"},
    )
    g.add_edge("pair_weather", "find_dishes")
    g.add_conditional_edges(
        "find_dishes", route_after_dishes,
        {"get_recipe": "get_recipe", "synthesize": "synthesize"},
    )
    g.add_edge("get_recipe", "synthesize")
    g.add_edge("synthesize", END)

    return g.compile(checkpointer=checkpointer)
