"""MLflow tracing setup for the deployed app.

`mlflow.langchain.autolog()` (in server/graph.py) captures a trace for every graph
run, but a trace needs a destination. This wires the deployed app to a Databricks
MLflow experiment so traces show up under Experiments -> <experiment> -> Traces,
and (when the experiment is linked to a Unity Catalog schema) in that catalog too.

Off unless MLFLOW_TRACING=on. Guarded: a failure disables tracing, never crashes
the app. Env:
  MLFLOW_TRACING=on|off
  MLFLOW_EXPERIMENT_ID       the experiment to log traces to (preferred)
  MLFLOW_EXPERIMENT          ... or by name/path
  MLFLOW_TRACING_SQL_WAREHOUSE_ID   warehouse for UC-backed traces (read by MLflow)
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("tracing")


def init_tracing() -> None:
    if os.environ.get("MLFLOW_TRACING", "").lower() not in ("on", "true", "1"):
        return
    try:
        import mlflow

        mlflow.set_tracking_uri("databricks")
        exp_id = os.environ.get("MLFLOW_EXPERIMENT_ID")
        exp_name = os.environ.get("MLFLOW_EXPERIMENT")
        if exp_id:
            mlflow.set_experiment(experiment_id=exp_id)
        elif exp_name:
            mlflow.set_experiment(exp_name)
        # autolog is also enabled at import in graph.py; re-assert here so tracing
        # is on regardless of import order.
        mlflow.langchain.autolog()
        log.info("MLflow tracing enabled (experiment=%s)", exp_id or exp_name or "default")
    except Exception as exc:  # noqa: BLE001
        log.warning("MLflow tracing disabled (init failed): %s", exc)
