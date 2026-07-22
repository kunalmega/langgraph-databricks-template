"""Typed, validated settings — the single place every env var is read.

Why this exists (production reference, not just a personal template):
  - Every environment variable the app reads is declared here ONCE, with a type
    and a default, so a missing or malformed value fails fast at startup with a
    clear message instead of surfacing as a cryptic error deep inside a request.
  - It distinguishes the two runtimes the template supports: a LOCAL run (via a
    Databricks CLI profile) and a deployed DATABRICKS APP (injected credentials).
  - `require(...)` gives call sites a way to assert the specific vars they need
    and raise one actionable error listing everything that's missing.

Load it via `get_settings()` (cached) — never read os.environ directly elsewhere.
"""
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Names are matched to environment variables case-insensitively (UAIG_ENDPOINT ->
# uaig_endpoint). protected_namespaces=() lets us keep the natural `model_service`
# field name without pydantic's "model_" namespace warning.


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        protected_namespaces=(),
    )

    # --- Runtime detection ---------------------------------------------------
    # Set by the Databricks Apps runtime; absent locally.
    databricks_app_name: Optional[str] = None

    # --- Auth (local only) ---------------------------------------------------
    databricks_profile: Optional[str] = None
    databricks_host: Optional[str] = None

    # --- LLM / Unity AI Gateway ---------------------------------------------
    uaig_endpoint: Optional[str] = None
    serving_endpoint: Optional[str] = None
    model_service: Optional[str] = None
    model_service_token: Optional[str] = None
    # Explicit identity choice for a model service call (see server/graph.py):
    #   "token" (default) -> MUST provide MODEL_SERVICE_TOKEN; predictable authz.
    #   "oauth"           -> deliberately use the caller/app OAuth identity.
    model_service_auth: str = "token"
    agent_name: str = "langgraph-sample-agent"

    # --- Lakebase (state store) ---------------------------------------------
    endpoint_name: Optional[str] = None
    pghost: Optional[str] = None
    pgport: str = "5432"
    pgdatabase: str = "langgraph_app"
    pguser: Optional[str] = None
    pgsslmode: str = "require"

    # --- Authz / operational toggles ----------------------------------------
    # When true, the chat endpoint requires a forwarded end-user identity.
    require_caller_identity: bool = False
    # The one-time /api/setup route is operationally sensitive (DDL on Lakebase).
    # It is OFF by default; enable only for a bootstrap run, then turn it back off.
    enable_setup_route: bool = False

    # Default foundation model if nothing else is configured.
    default_llm_endpoint: str = "databricks-claude-sonnet-5"

    @property
    def is_databricks_app(self) -> bool:
        return bool(self.databricks_app_name)

    @property
    def llm_endpoint(self) -> str:
        """The Unity AI Gateway endpoint the agent's LLM calls route through."""
        return self.uaig_endpoint or self.serving_endpoint or self.default_llm_endpoint

    def require(self, *names: str) -> None:
        """Assert that the named settings are present, else raise one clear error."""
        missing = [n.upper() for n in names if not getattr(self, n, None)]
        if missing:
            raise RuntimeError(
                "Missing required configuration: "
                + ", ".join(missing)
                + ". Set these in .env (local) or app.yaml (deployed). "
                "See .env.example."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings once per process."""
    return Settings()
