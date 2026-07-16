"""Chat API: runs the LangGraph agent, persisting state to Lakebase per thread_id.

Handlers are sync `def` so FastAPI runs them in a threadpool — correct for
psycopg's sync connection pool and the PostgresSaver checkpointer.
"""
import uuid

from fastapi import APIRouter
from langgraph.checkpoint.postgres import PostgresSaver
from pydantic import BaseModel

from ..db import pool
from ..graph import build_graph

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    thread_id: str


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    thread_id = req.thread_id or str(uuid.uuid4())

    # PostgresSaver stores checkpoints in the Lakebase database. It borrows a
    # connection from the shared OAuth pool for the duration of the call.
    with pool.connection() as conn:
        checkpointer = PostgresSaver(conn)
        graph = build_graph(checkpointer=checkpointer)
        result = graph.invoke(
            {"messages": [{"role": "user", "content": req.message}]},
            config={"configurable": {"thread_id": thread_id}},
        )

    reply = result["messages"][-1].content
    return ChatResponse(reply=reply, thread_id=thread_id)


@router.post("/setup")
def setup() -> dict:
    """One-time: create the LangGraph checkpoint tables in Lakebase.

    setup() issues CREATE INDEX CONCURRENTLY, which cannot run in a transaction,
    so the connection must be in autocommit mode.
    """
    with pool.connection() as conn:
        conn.autocommit = True
        PostgresSaver(conn).setup()
    return {"status": "checkpoint tables created"}
