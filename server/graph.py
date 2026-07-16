"""The LangGraph agent: a minimal ReAct agent with one tool.

The same graph is reused two ways:
  1. Behind the FastAPI app (checkpointed to Lakebase for durable conversation state).
  2. Wrapped as an MLflow ChatAgent (agent.py).

The LLM is routed through Unity AI Gateway via databricks-langchain's
ChatDatabricks(use_ai_gateway=True): the model argument is the name of a Unity
AI Gateway endpoint, and the client handles gateway routing + auth automatically,
so every LLM call is governed (rate limits, usage tracking, guardrails).
"""
import ast
import operator

import mlflow
from databricks_langchain import ChatDatabricks
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from .config import get_serving_endpoint

# Trace LangChain/LangGraph calls into MLflow.
mlflow.langchain.autolog()

# --- Tool ---------------------------------------------------------------------

_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
}


def _safe_eval(node):
    if isinstance(node, ast.Constant):  # numbers
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("only numeric constants allowed")
    if isinstance(node, ast.BinOp):
        return _ALLOWED_OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        return _ALLOWED_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("unsupported expression")


@tool
def calculator(expression: str) -> str:
    """Evaluate a basic arithmetic expression (e.g. '2 + 3 * 4'). Supports + - * / ** %."""
    try:
        result = _safe_eval(ast.parse(expression, mode="eval").body)
        return str(result)
    except Exception as exc:  # noqa: BLE001
        return f"Error: could not evaluate '{expression}' ({exc})"


TOOLS = [calculator]


# --- LLM ----------------------------------------------------------------------


import os

# Name this app/agent in the Unity AI Gateway usage view via request tags. These
# populate the request_tags column in system.ai_gateway.usage so all custom apps can
# be attributed/filtered by agent in one place.
AGENT_NAME = os.environ.get("AGENT_NAME", "langgraph-sample-agent")
APP_NAME = os.environ.get("DATABRICKS_APP_NAME", "langgraph-sample")


def build_llm() -> ChatDatabricks:
    """LangChain chat model routed through Unity AI Gateway.

    `model` is the name of the Unity AI Gateway endpoint (set via
    UAIG_ENDPOINT / SERVING_ENDPOINT). use_ai_gateway=True makes the client
    route through the gateway and handle auth automatically. We attach gateway
    request tags so this agent is identifiable in the gateway usage view.
    Note: some models (e.g. claude-sonnet-5 reasoning) reject temperature.
    """
    return ChatDatabricks(
        model=get_serving_endpoint(),
        use_ai_gateway=True,
        max_tokens=1024,
        extra_params={
            "extra_headers": {
                "Databricks-Ai-Gateway-Request-Tags": (
                    '{"agent":"%s","app":"%s"}' % (AGENT_NAME, APP_NAME)
                )
            }
        },
    )


SYSTEM_PROMPT = (
    "You are a concise, friendly assistant. When arithmetic is needed, "
    "use the calculator tool rather than computing it yourself."
)


def build_graph(checkpointer=None):
    """Create the ReAct agent graph, optionally with a checkpointer for durable state."""
    return create_react_agent(
        build_llm(),
        TOOLS,
        prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )
