"""Test BOTH agent APIs using ONLY a service-principal token + URL.

No Databricks CLI, no project virtualenv, no SDK. This is how a backend service (or
anyone off-platform) calls the agent: get an OAuth token via client-credentials, then
POST to the two URLs. Only dependency is `requests` (pip install requests).

Set these env vars (nothing hardcoded except the app/endpoint URLs, which are overridable):

    export DATABRICKS_HOST="https://fevm-fevm-cme-conde.cloud.databricks.com"
    export CLIENT_ID="<service-principal-application-id>"
    export CLIENT_SECRET="<service-principal-oauth-secret>"
    # optional overrides:
    # export APP_URL="https://<app>.aws.databricksapps.com"
    # export AGENT_ENDPOINT="<agent-serving-endpoint-name>"

    python test_api_sp.py

The service principal must have: CAN_USE on the app (Way 1) and CAN_QUERY on the
serving endpoint (Way 2).
"""
import os
import sys

import requests

# Read from environment variables (do NOT hardcode secrets in the file).
# Defaults are provided for HOST and CLIENT_ID for convenience; CLIENT_SECRET must
# always come from the environment.
HOST = os.environ.get("DATABRICKS_HOST", "https://fevm-fevm-cme-conde.cloud.databricks.com").rstrip("/")
CLIENT_ID = os.environ.get("CLIENT_ID", "db5da372-cc74-434c-829d-2039902c3374")
CLIENT_SECRET = os.environ["CLIENT_SECRET"]

APP_URL = os.environ.get(
    "APP_URL", "https://langgraph-sample-7474657767854090.aws.databricksapps.com"
).rstrip("/")
AGENT_ENDPOINT = os.environ.get(
    "AGENT_ENDPOINT", "agents_fevm_cme_conde_catalog-langgraph_demo-langgraph_sample_a"
)


def get_token() -> str:
    """OAuth token for the service principal (machine-to-machine, no browser)."""
    r = requests.post(
        f"{HOST}/oidc/v1/token",
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={"grant_type": "client_credentials", "scope": "all-apis"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def way1_app(token: str) -> bool:
    """Way 1 — FastAPI app (stateful, Lakebase memory). Body uses 'message'."""
    print("\n=== Way 1: FastAPI App (stateful, Lakebase memory) ===")
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    r1 = requests.post(f"{APP_URL}/api/chat", headers=h,
                       json={"message": "What is 21 * 3? Use the calculator tool."}, timeout=120)
    r1.raise_for_status()
    d1 = r1.json()
    tid = d1["thread_id"]
    print(f"  turn 1 -> {d1['reply']}   (thread {tid[:8]})")

    r2 = requests.post(f"{APP_URL}/api/chat", headers=h,
                       json={"message": "What did I just ask?", "thread_id": tid}, timeout=120)
    r2.raise_for_status()
    reply2 = r2.json()["reply"]
    print(f"  turn 2 -> {reply2}")
    remembered = "21" in reply2 and "3" in reply2
    print(f"  memory persisted (Lakebase): {'PASS' if remembered else 'FAIL'}")
    return "63" in d1["reply"] and remembered


def way2_agent(token: str) -> bool:
    """Way 2 — registered agent serving endpoint (governed, stateless). Body uses 'messages'."""
    print("\n=== Way 2: Registered Agent endpoint (governed, stateless) ===")
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{HOST}/serving-endpoints/{AGENT_ENDPOINT}/invocations"

    r = requests.post(url, headers=h,
                      json={"messages": [{"role": "user", "content": "What is 21 * 3? Use the calculator tool."}]},
                      timeout=120)
    r.raise_for_status()
    reply = r.json()["messages"][-1]["content"]
    print(f"  single turn -> {reply}")
    return "63" in reply


def main() -> None:
    print(f"Host:  {HOST}")
    print(f"SP:    {CLIENT_ID}")
    print(f"App:   {APP_URL}")
    print(f"Agent: {AGENT_ENDPOINT}")

    token = get_token()
    print(f"\nService-principal token acquired (len {len(token)}).")

    results = {}
    for name, fn in (("Way 1 (App)", way1_app), ("Way 2 (Agent endpoint)", way2_agent)):
        try:
            results[name] = fn(token)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {exc}")
            results[name] = False

    print("\n=== SUMMARY ===")
    for name, ok in results.items():
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
