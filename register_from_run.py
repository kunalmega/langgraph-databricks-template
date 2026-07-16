"""Retry helper for Part C: register an ALREADY-logged MLflow model to Unity
Catalog and deploy it — reuses a server-side model (models:/<id>) so UC
registration is a server-side copy, not a local re-upload. Use this if
deploy_agent.py timed out after logging (avoids re-logging).

Config via env (see .env.example): UC_CATALOG, UC_SCHEMA, DATABRICKS_PROFILE.
"""
import argparse
import os
import time

import mlflow
from databricks import agents

# All config comes from env (see .env.example) — nothing hardcoded.
MODEL_NAME = os.environ.get("AGENT_MODEL_NAME", "langgraph_sample_agent")


def _uc_name() -> str:
    catalog = os.environ.get("UC_CATALOG")
    schema = os.environ.get("UC_SCHEMA")
    if not catalog or not schema:
        raise SystemExit("Set UC_CATALOG and UC_SCHEMA (see .env.example).")
    return f"{catalog}.{schema}.{MODEL_NAME}"


def register_with_retry(model_uri: str, uc_name: str, attempts: int = 8):
    last = None
    for i in range(attempts):
        try:
            return mlflow.register_model(model_uri=model_uri, name=uc_name)
        except Exception as exc:  # noqa: BLE001
            last = exc
            msg = str(exc)
            if not any(s in msg for s in ("UC-TKTLK", "TemporarilyUnavailable", "Timed out", "503", "500")):
                raise
            if i == attempts - 1:
                raise
            wait = min(45, 8 * (i + 1))
            print(f"[retry {i+1}/{attempts}] {msg[:100]} — waiting {wait}s", flush=True)
            time.sleep(wait)
    raise last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-id", required=True, help="MLflow 3 logged model id, e.g. m-abc123")
    args = ap.parse_args()

    uc_name = _uc_name()
    profile = os.environ.get("DATABRICKS_PROFILE", "DEFAULT")
    mlflow.set_tracking_uri(f"databricks://{profile}")
    mlflow.set_registry_uri(f"databricks-uc://{profile}")

    model_uri = f"models:/{args.model_id}"
    print(f"Registering {model_uri} -> {uc_name}", flush=True)
    reg = register_with_retry(model_uri, uc_name)
    print(f"Registered version {reg.version}", flush=True)

    # Deploy → agent appears in the Unity AI Gateway Agents tab automatically.
    # Governance (rate limits, guardrails, usage tracking) is configured in the
    # Unity AI Gateway UI — put_ai_gateway is NOT used for agent endpoints.
    dep = agents.deploy(model_name=uc_name, model_version=int(reg.version), scale_to_zero=True)
    print(f"Deployed endpoint: {dep.endpoint_name}", flush=True)
    print(f"Query URL: {dep.query_endpoint}", flush=True)
    print("Done! Configure governance in Unity AI Gateway UI → Agents tab.", flush=True)


if __name__ == "__main__":
    main()
