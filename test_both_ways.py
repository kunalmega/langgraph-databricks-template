"""Test the agent BOTH ways from outside Databricks, in one script.

  Way 1 — FastAPI App (stateful, Lakebase memory): POST /api/chat with a thread_id.
  Way 2 — Registered Agent serving endpoint (governed, stateless): POST /invocations.

Auth: uses the Databricks SDK to resolve host + token from your CLI profile
(DATABRICKS_PROFILE), so it runs from anywhere with the CLI configured. A backend
service would instead set DATABRICKS_HOST + DATABRICKS_CLIENT_ID/SECRET (M2M).

EXACT RUN STEPS (see RUN_TEST.md for the full runbook):

    # one-time: log in + install deps (public PyPI is blocked -> use the proxy)
    databricks auth login --host https://fevm-fevm-cme-conde.cloud.databricks.com --profile Fevm-fevm-conde
    UV_INDEX_URL="https://pypi-proxy.cloud.databricks.com/simple" uv sync

    # run (do NOT use `uv run` — it re-checks blocked PyPI; call the venv python directly)
    export DATABRICKS_PROFILE=Fevm-fevm-conde
    .venv/bin/python test_both_ways.py
"""
import os

import requests
from databricks.sdk import WorkspaceClient

# --- Config (override via env) ----------------------------------------------
APP_URL = os.environ.get(
    "APP_URL",
    "https://langgraph-sample-7474657767854090.aws.databricksapps.com",
)
AGENT_ENDPOINT = os.environ.get(
    "AGENT_ENDPOINT",
    "agents_fevm_cme_conde_catalog-langgraph_demo-langgraph_sample_a",
)
PROFILE = os.environ.get("DATABRICKS_PROFILE", "Fevm-fevm-conde")

_w = WorkspaceClient(profile=PROFILE)
_cfg = _w.config


def _auth_headers() -> dict:
    """Authorization header for the current identity (works off-platform too)."""
    return {**_cfg.authenticate(), "Content-Type": "application/json"}


# --- Way 1: FastAPI App (stateful via Lakebase) -----------------------------
def app_chat(message: str, thread_id: str | None = None) -> dict:
    r = requests.post(
        f"{APP_URL}/api/chat",
        headers=_auth_headers(),
        json={"message": message, "thread_id": thread_id},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def test_way1_app() -> bool:
    print("\n=== Way 1: FastAPI App (stateful, Lakebase memory) ===")
    turn1 = app_chat("What is 21 * 3? Use the calculator tool.")
    tid = turn1["thread_id"]
    print(f"  turn 1 -> {turn1['reply']}   (thread {tid[:8]})")

    turn2 = app_chat("What did I just ask?", thread_id=tid)
    print(f"  turn 2 -> {turn2['reply']}")

    remembered = "21" in turn2["reply"] and "3" in turn2["reply"]
    print(f"  memory persisted across turns (Lakebase): {'PASS' if remembered else 'FAIL'}")
    return "63" in turn1["reply"] and remembered


# --- Way 2: Registered Agent endpoint (governed, stateless) -----------------
def agent_invoke(messages: list[dict]) -> str:
    ep = f"{_cfg.host}/serving-endpoints/{AGENT_ENDPOINT}/invocations"
    r = requests.post(ep, headers=_auth_headers(), json={"messages": messages}, timeout=120)
    r.raise_for_status()
    return r.json()["messages"][-1]["content"]


def test_way2_agent() -> bool:
    print("\n=== Way 2: Registered Agent endpoint (governed, stateless) ===")
    single = agent_invoke(
        [{"role": "user", "content": "What is 21 * 3? Use the calculator tool."}]
    )
    print(f"  single turn -> {single}")

    # Stateless: we supply the history ourselves.
    multi = agent_invoke(
        [
            {"role": "user", "content": "What is 21 * 3?"},
            {"role": "assistant", "content": "21 * 3 = 63"},
            {"role": "user", "content": "What did I just ask?"},
        ]
    )
    print(f"  multi turn (history supplied) -> {multi}")
    return "63" in single


def main() -> None:
    print(f"Profile: {PROFILE}")
    print(f"App URL: {APP_URL}")
    print(f"Agent endpoint: {AGENT_ENDPOINT}")

    results = {}
    for name, fn in (("Way 1 (App)", test_way1_app), ("Way 2 (Agent endpoint)", test_way2_agent)):
        try:
            results[name] = fn()
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {exc}")
            results[name] = False

    print("\n=== SUMMARY ===")
    for name, ok in results.items():
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
