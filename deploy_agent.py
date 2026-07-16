"""Register the agent (and its LLM) into Unity AI Gateway.

There are TWO registrations with Unity AI Gateway — this script handles both concerns:

  1. THE LLM  — governed by ROUTING, not by a separate registration step.
     The agent calls the model via ChatDatabricks(use_ai_gateway=True) pointed at the
     gateway endpoint named by UAIG_ENDPOINT (see server/graph.py). That endpoint is
     what this script DECLARES as a dependency below
     (resources=[DatabricksServingEndpoint(UAIG_ENDPOINT)]), so the registered agent is
     linked to its LLM endpoint and the LLM's traffic is governed by the gateway.
     -> To point at a different LLM, change UAIG_ENDPOINT (one value). Foundation-model
        endpoints are gateway-accessible out of the box; for an EXTERNAL model you first
        create a gateway/serving endpoint for it in the workspace, then use its name here.

  2. THE AGENT — registered EXPLICITLY here: log -> register to Unity Catalog ->
     agents.deploy() -> a Model Serving endpoint that appears on the Unity AI Gateway
     "Agents" inventory. Governance (rate limits, guardrails, usage tracking) is then
     configured in the Unity AI Gateway UI. (The old put_ai_gateway API is not used for
     agent endpoints.)

Run locally against the workspace (reads UC_CATALOG / UC_SCHEMA / UAIG_ENDPOINT from .env):

    set -a; source .env; set +a
    export DATABRICKS_CONFIG_PROFILE="$DATABRICKS_PROFILE"
    uv run python deploy_agent.py
"""
import argparse
import os

import mlflow
from databricks import agents
from databricks.sdk import WorkspaceClient
from mlflow.models.resources import DatabricksServingEndpoint

from server.config import get_serving_endpoint

MODEL_NAME = os.environ.get("AGENT_MODEL_NAME", "langgraph_sample_agent")


def _register_with_retry(model_uri: str, uc_name: str, attempts: int = 6):
    """mlflow.register_model, retrying transient UC/timeout errors with backoff."""
    import time

    last_exc = None
    for i in range(attempts):
        try:
            return mlflow.register_model(model_uri=model_uri, name=uc_name)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            msg = str(exc)
            transient = any(
                s in msg
                for s in ("UC-TKTLK", "TemporarilyUnavailable", "Timed out", "503", "500")
            )
            if not transient or i == attempts - 1:
                raise
            wait = min(30, 5 * (i + 1))
            print(f"[retry {i + 1}/{attempts}] transient error, waiting {wait}s: {msg[:120]}")
            time.sleep(wait)
    raise last_exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        default=os.environ.get("UC_CATALOG"),
        help="Unity Catalog catalog (or set UC_CATALOG)",
    )
    parser.add_argument(
        "--schema",
        default=os.environ.get("UC_SCHEMA"),
        help="Unity Catalog schema (or set UC_SCHEMA)",
    )
    args = parser.parse_args()
    if not args.catalog or not args.schema:
        parser.error("provide --catalog/--schema or set UC_CATALOG/UC_SCHEMA")

    uc_name = f"{args.catalog}.{args.schema}.{MODEL_NAME}"
    llm_endpoint = get_serving_endpoint()

    # Disambiguate MLflow auth: several ~/.databrickscfg profiles can match the same
    # host, so pin MLflow's tracking + registry URIs to the chosen profile.
    profile = os.environ.get("DATABRICKS_PROFILE", "DEFAULT")
    mlflow.set_tracking_uri(f"databricks://{profile}")
    mlflow.set_registry_uri(f"databricks-uc://{profile}")

    # A workspace experiment is required when logging from outside a notebook.
    who = WorkspaceClient(profile=profile).current_user.me().user_name
    mlflow.set_experiment(f"/Users/{who}/langgraph-sample-agent")

    # 1. Log the agent, declaring the foundation model it depends on.
    #    A prior model_uri can be passed via MODEL_URI to skip re-logging on retry.
    model_uri = os.environ.get("MODEL_URI")
    if not model_uri:
        with mlflow.start_run(run_name="langgraph-sample-agent"):
            logged = mlflow.pyfunc.log_model(
                name="agent",
                python_model="agent.py",
                code_paths=["server/"],
                pip_requirements=[
                    "mlflow>=2.20.2",
                    "langgraph==1.2.8",
                    "langgraph-prebuilt==1.1.0",
                    "langchain==1.3.11",
                    "langchain-core==1.4.8",
                    "databricks-langchain==0.20.0",
                    "databricks-sdk>=0.40",
                ],
                resources=[DatabricksServingEndpoint(endpoint_name=llm_endpoint)],
            )
        model_uri = logged.model_uri
    print(f"MODEL_URI={model_uri}")

    # 2. Register to Unity Catalog, retrying transient UC / timeout errors.
    registered = _register_with_retry(model_uri, uc_name)
    print(f"Registered {uc_name} version {registered.version}")

    # 3. Deploy to a Model Serving endpoint (Agent Framework).
    #    After this step the agent appears in the Unity AI Gateway Agents tab.
    #    Governance (rate limits, guardrails, usage tracking) is configured in the
    #    new Unity AI Gateway UI — put_ai_gateway is NOT supported for agent endpoints.
    deployment = agents.deploy(
        model_name=uc_name,
        model_version=int(registered.version),
        scale_to_zero=True,
    )
    endpoint_name = deployment.endpoint_name
    print(f"Deployed serving endpoint: {endpoint_name}")
    print(f"Query URL: {deployment.query_endpoint}")
    print(f"\nDone! Configure governance in Unity AI Gateway UI → Agents tab.")


if __name__ == "__main__":
    main()
