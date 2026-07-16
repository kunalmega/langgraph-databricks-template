"""MLflow ChatAgent wrapper around the LangGraph ReAct agent.

This is the model logged to MLflow, registered to Unity Catalog, and deployed
to a Model Serving endpoint governed by AI Gateway. It reuses the SAME graph
definition as the Databricks App (server/graph.py), but runs without a
checkpointer — the serving endpoint is stateless; conversation history arrives
in the `messages` payload.
"""
import uuid
from typing import Any, Optional

import mlflow
from mlflow.pyfunc import ChatAgent
from mlflow.types.agent import ChatAgentMessage, ChatAgentResponse

from server.graph import build_graph

mlflow.langchain.autolog()


class LangGraphChatAgent(ChatAgent):
    def __init__(self) -> None:
        # Built lazily on first predict so importing/logging this module does not
        # require AI_GATEWAY_URL / credentials to be present.
        self._graph = None

    def _get_graph(self):
        if self._graph is None:
            self._graph = build_graph(checkpointer=None)
        return self._graph

    def predict(
        self,
        messages: list[ChatAgentMessage],
        context: Optional[Any] = None,
        custom_inputs: Optional[dict] = None,
    ) -> ChatAgentResponse:
        request = {"messages": [{"role": m.role, "content": m.content} for m in messages]}
        output = self._get_graph().invoke(request)
        reply = output["messages"][-1].content
        return ChatAgentResponse(
            messages=[
                ChatAgentMessage(role="assistant", content=reply, id=str(uuid.uuid4()))
            ]
        )


AGENT = LangGraphChatAgent()
mlflow.models.set_model(AGENT)
