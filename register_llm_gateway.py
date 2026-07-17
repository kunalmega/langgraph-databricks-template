"""Create / "lock" a GOVERNED LLM endpoint in Unity AI Gateway (the MODEL side).

This is the missing counterpart to deploy_agent.py:
  - deploy_agent.py  -> registers the AGENT   (your app) on the Gateway Agents inventory
  - register_llm_gateway.py -> registers the LLM ENDPOINT with governance + FALLBACK

Databricks foundation-model endpoints (e.g. `databricks-claude-sonnet-5`) are shared
*system* endpoints you can't reconfigure. To get model routing, fallback, and per-endpoint
guardrails/limits, you create YOUR OWN serving endpoint that serves one or more models and
attach an AI Gateway config to it. Your agent then points UAIG_ENDPOINT at this endpoint.

What this creates:
  - a serving endpoint (GATEWAY_LLM_ENDPOINT) serving a PRIMARY model + a FALLBACK model
  - traffic routing (100% primary) with fallback enabled (on 429/5xx -> next served model)
  - AI Gateway governance: PII guardrails (in/out), rate limit, usage tracking

Run:
    set -a; source .env; set +a
    export DATABRICKS_CONFIG_PROFILE="$DATABRICKS_PROFILE"
    uv run python register_llm_gateway.py

Then set UAIG_ENDPOINT (in .env / app.yaml) to GATEWAY_LLM_ENDPOINT so the agent's LLM
calls flow through YOUR governed, fallback-protected endpoint.
"""
import os

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import (
    AiGatewayGuardrailParameters,
    AiGatewayGuardrailPiiBehavior,
    AiGatewayGuardrailPiiBehaviorBehavior,
    AiGatewayGuardrails,
    AiGatewayRateLimit,
    AiGatewayRateLimitKey,
    AiGatewayRateLimitRenewalPeriod,
    AiGatewayUsageTrackingConfig,
    DatabricksModelServingConfig,
    EndpointCoreConfigInput,
    ExternalModel,
    ExternalModelProvider,
    FallbackConfig,
    Route,
    ServedEntityInput,
    TrafficConfig,
)

# --- Config (from .env; sensible defaults) ----------------------------------
ENDPOINT = os.environ.get("GATEWAY_LLM_ENDPOINT", "langgraph-governed-llm")
PROVIDER = os.environ.get("LLM_PROVIDER", "databricks-model-serving")
# Primary + fallback model endpoint names (Databricks-hosted foundation models by default).
PRIMARY_MODEL = os.environ.get("LLM_PRIMARY", "databricks-claude-sonnet-5")
FALLBACK_MODEL = os.environ.get("LLM_FALLBACK", "databricks-claude-haiku-4-5")
PROFILE = os.environ.get("DATABRICKS_PROFILE", "DEFAULT")
# An external-model served entity needs an API token, referenced from a secret scope
# (never hardcode it). One-time setup:
#   databricks secrets create-scope <scope>
#   databricks secrets put-secret <scope> <key>   # value = a Databricks PAT (or the
#                                                  #   provider API key for openai/anthropic)
# Then set LLM_TOKEN_SECRET="{{secrets/<scope>/<key>}}".
TOKEN_SECRET = os.environ.get("LLM_TOKEN_SECRET")  # e.g. "{{secrets/llm/pat}}"


def _served(name: str, model: str, workspace_url: str) -> ServedEntityInput:
    """A served entity that proxies a Databricks foundation-model endpoint."""
    return ServedEntityInput(
        name=name,
        external_model=ExternalModel(
            name=model,
            provider=ExternalModelProvider(PROVIDER),
            task="llm/v1/chat",
            databricks_model_serving_config=DatabricksModelServingConfig(
                databricks_workspace_url=workspace_url,
                databricks_api_token=TOKEN_SECRET,  # secret reference: {{secrets/scope/key}}
            ),
        ),
    )


def main() -> None:
    if not TOKEN_SECRET:
        raise SystemExit(
            "Set LLM_TOKEN_SECRET to a secret reference, e.g. '{{secrets/llm/pat}}'.\n"
            "One-time: databricks secrets create-scope llm && "
            "databricks secrets put-secret llm pat  (value = a Databricks PAT)."
        )
    w = WorkspaceClient(profile=PROFILE)
    workspace_url = w.config.host  # e.g. https://<workspace>.cloud.databricks.com

    served = [_served("primary", PRIMARY_MODEL, workspace_url),
              _served("fallback", FALLBACK_MODEL, workspace_url)]
    # 100% to primary; fallback_config sends failed calls to the next served entity.
    traffic = TrafficConfig(routes=[
        Route(served_entity_name="primary", traffic_percentage=100),
        Route(served_entity_name="fallback", traffic_percentage=0),
    ])

    gateway = dict(
        guardrails=AiGatewayGuardrails(
            input=AiGatewayGuardrailParameters(
                pii=AiGatewayGuardrailPiiBehavior(
                    behavior=AiGatewayGuardrailPiiBehaviorBehavior.BLOCK)),
            output=AiGatewayGuardrailParameters(
                pii=AiGatewayGuardrailPiiBehavior(
                    behavior=AiGatewayGuardrailPiiBehaviorBehavior.BLOCK)),
        ),
        rate_limits=[AiGatewayRateLimit(
            calls=100,
            renewal_period=AiGatewayRateLimitRenewalPeriod.MINUTE,
            key=AiGatewayRateLimitKey.ENDPOINT,
        )],
        usage_tracking_config=AiGatewayUsageTrackingConfig(enabled=True),
        fallback_config=FallbackConfig(enabled=True),
    )

    existing = [e.name for e in (w.serving_endpoints.list() or [])]
    if ENDPOINT in existing:
        print(f"Endpoint '{ENDPOINT}' exists — updating config + gateway.")
        w.serving_endpoints.update_config(
            name=ENDPOINT, served_entities=served, traffic_config=traffic)
    else:
        print(f"Creating governed LLM endpoint '{ENDPOINT}' "
              f"(primary={PRIMARY_MODEL}, fallback={FALLBACK_MODEL})...")
        w.serving_endpoints.create(
            name=ENDPOINT,
            config=EndpointCoreConfigInput(
                name=ENDPOINT, served_entities=served, traffic_config=traffic),
        )

    w.serving_endpoints.put_ai_gateway(name=ENDPOINT, **gateway)
    print(f"AI Gateway governance applied to '{ENDPOINT}': "
          f"PII guardrails, rate limit (100/min/endpoint), usage tracking, fallback ON.")
    print(f"\nNext: set UAIG_ENDPOINT={ENDPOINT} in .env / app.yaml so the agent routes "
          f"through this governed, fallback-protected endpoint.")


if __name__ == "__main__":
    main()
