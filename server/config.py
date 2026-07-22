"""Dual-mode auth + environment detection.

Works both locally (via a Databricks CLI profile) and inside a Databricks App
(via auto-injected service-principal credentials).

Note on local auth: this reads DATABRICKS_PROFILE and fails clearly if it is
missing, rather than silently defaulting to one contributor's personal profile.
A reusable reference should never assume a specific local workspace.
"""
from databricks.sdk import WorkspaceClient

from .settings import get_settings


def get_workspace_client() -> WorkspaceClient:
    """Return an authenticated WorkspaceClient for the current environment."""
    settings = get_settings()
    if settings.is_databricks_app:
        # Remote: auto-injected service principal credentials.
        return WorkspaceClient()
    # Local: require an explicit CLI profile — do not guess a personal one.
    if not settings.databricks_profile:
        raise RuntimeError(
            "DATABRICKS_PROFILE is not set. For local runs, log in with "
            "`databricks auth login --profile <name>` and set DATABRICKS_PROFILE=<name> "
            "in your .env. (In a deployed Databricks App this is not needed.)"
        )
    return WorkspaceClient(profile=settings.databricks_profile)


def get_oauth_token() -> str:
    """OAuth bearer token for the current identity (used as the AI Gateway API key)."""
    client = get_workspace_client()
    auth_headers = client.config.authenticate()
    if auth_headers and "Authorization" in auth_headers:
        return auth_headers["Authorization"].replace("Bearer ", "")
    # Fallback for PAT-based configs.
    return client.config.token


def get_workspace_host() -> str:
    """Workspace host URL, always with an https:// scheme."""
    settings = get_settings()
    if settings.is_databricks_app:
        host = settings.databricks_host or ""
        if host and not host.startswith("http"):
            host = f"https://{host}"
        return host
    return get_workspace_client().config.host


def get_serving_endpoint() -> str:
    """Name of the Unity AI Gateway endpoint the agent's LLM calls route through.

    With ChatDatabricks(use_ai_gateway=True), this is the gateway endpoint name
    (UAIG_ENDPOINT). Falls back to SERVING_ENDPOINT / a foundation-model name.
    """
    return get_settings().llm_endpoint
