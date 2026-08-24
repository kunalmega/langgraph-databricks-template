"""Chat API: runs the LangGraph agent, persisting state to Lakebase per thread_id.

Handlers are sync `def` so FastAPI runs them in a threadpool — correct for
psycopg's sync connection pool and the PostgresSaver checkpointer.

Caller identity & authorization
-------------------------------
A Databricks App receives the end user's identity in forwarded headers
(X-Forwarded-User / X-Forwarded-Email / X-Forwarded-Access-Token). This route
extracts that into a `CallerContext` and passes it into the agent run, so tool
execution can make per-caller authorization decisions and calls are attributable
to a real user. When REQUIRE_CALLER_IDENTITY is set, an unauthenticated request
is rejected (401) instead of running anonymously — the right default for
multi-tenant deployments.
"""
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from memory import build_checkpointer
from ..db import get_pool
from ..graph import build_graph
from ..memory_wire import get_store
from ..settings import get_settings

router = APIRouter()


@dataclass
class CallerContext:
    """Who is making this request (from Databricks Apps forwarded headers)."""

    user: Optional[str]
    email: Optional[str]
    request_id: str

    @property
    def is_authenticated(self) -> bool:
        return bool(self.user or self.email)


def get_caller(
    request: Request,
    x_forwarded_user: Optional[str] = Header(default=None),
    x_forwarded_email: Optional[str] = Header(default=None),
) -> CallerContext:
    """Build the caller context and enforce identity when required."""
    settings = get_settings()
    # request.state.request_id is set by the correlation middleware (app.py).
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    caller = CallerContext(
        user=x_forwarded_user, email=x_forwarded_email, request_id=request_id
    )
    if settings.require_caller_identity and not caller.is_authenticated:
        raise HTTPException(
            status_code=401,
            detail="Caller identity required but no forwarded user header present.",
        )
    return caller


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    thread_id: str


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, caller: CallerContext = Depends(get_caller)) -> ChatResponse:
    # Namespace threads by caller so one user cannot resume another's conversation
    # by guessing a thread_id. Anonymous callers (identity not required) share the
    # "anon" namespace.
    owner = caller.email or caller.user or "anon"
    thread_id = req.thread_id or str(uuid.uuid4())

    # SHORT-TERM memory: a checkpointer stores this thread's state in Lakebase.
    # It borrows a connection from the shared OAuth pool for the call. LONG-TERM
    # memory (get_store()) is passed too — None (no-op) unless configured.
    with get_pool().connection() as conn:
        checkpointer = build_checkpointer(conn)
        graph = build_graph(checkpointer=checkpointer, store=get_store())
        result = graph.invoke(
            {"messages": [{"role": "user", "content": req.message}]},
            config={
                "configurable": {
                    "thread_id": f"{owner}:{thread_id}",
                    # Propagate caller context so tools can authorize per-user.
                    "caller_user": caller.user,
                    "caller_email": caller.email,
                    "request_id": caller.request_id,
                }
            },
        )

    reply = result["messages"][-1].content
    return ChatResponse(reply=reply, thread_id=thread_id)


@router.post("/setup")
def setup() -> dict:
    """One-time: create the LangGraph checkpoint tables in Lakebase.

    OPERATIONALLY SENSITIVE — this issues DDL (CREATE INDEX CONCURRENTLY) against
    the state store. It is gated behind ENABLE_SETUP_ROUTE and returns 404 unless
    explicitly enabled, so it is not a general public route. Prefer running the
    provisioning script (setup/) for bootstrap; enable this only if you must run
    it in-app, then disable it again.
    """
    if not get_settings().enable_setup_route:
        raise HTTPException(status_code=404, detail="Not found.")

    # CREATE INDEX CONCURRENTLY cannot run in a transaction -> autocommit.
    with get_pool().connection() as conn:
        conn.autocommit = True
        PostgresSaver(conn).setup()
    return {"status": "checkpoint tables created"}
