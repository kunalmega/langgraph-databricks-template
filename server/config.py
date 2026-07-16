"""Dual-mode auth + environment detection.

Works both locally (via a Databricks CLI profile) and inside a Databricks App
(via auto-injected service-principal credentials).
"""
import os

from databricks.sdk import WorkspaceClient

# In a Databricks App the runtime sets DATABRICKS_APP_NAME.
IS_DATABRICKS_APP = bool(os.environ.get("DATABRICKS_APP_NAME"))


def get_workspace_client() -> WorkspaceClient:
    """Return an authenticated WorkspaceClient for the current environment."""
    if IS_DATABRICKS_APP:
        # Remote: auto-injected service principal credentials.
        return WorkspaceClient()
    # Local: use the CLI profile (default: Fevm-fevm-conde).
    profile = os.environ.get("DATABRICKS_PROFILE", "Fevm-fevm-conde")
    return WorkspaceClient(profile=profile)


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
    if IS_DATABRICKS_APP:
        host = os.environ.get("DATABRICKS_HOST", "")
        if host and not host.startswith("http"):
            host = f"https://{host}"
        return host
    return get_workspace_client().config.host


def get_serving_endpoint() -> str:
    """Name of the Unity AI Gateway endpoint the agent's LLM calls route through.

    With ChatDatabricks(use_ai_gateway=True), this is the gateway endpoint name
    (UAIG_ENDPOINT). Falls back to SERVING_ENDPOINT / a foundation-model name.
    """
    return (
        os.environ.get("UAIG_ENDPOINT")
        or os.environ.get("SERVING_ENDPOINT")
        or "databricks-claude-sonnet-5"
    )
