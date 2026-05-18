from __future__ import annotations

from collections.abc import Callable
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.models.chat import ChatRequest, ChatResponse
from app.services.manager_agent_loop import ManagerAgentLoop
from app.services.manager_planner import DeepSeekManagerPlanner, ManagerPlanDraft, ManagerPlanningError
from app.services.manager_tools import ManagerToolLayer
from app.services.project_service import ProjectService


WorkflowRoute = Literal[
    "__end__",
]


class ManagerWorkflowState(TypedDict, total=False):
    project_id: str
    request: ChatRequest
    snapshot: dict
    response: ChatResponse


class ManagerWorkflow:
    """LangGraph orchestration layer for Manager chat and blueprint mutation."""

    def __init__(
        self,
        project_service: ProjectService,
        planner: DeepSeekManagerPlanner,
        tool_layer: ManagerToolLayer,
        save_proposal: Callable[[str, dict, ManagerPlanDraft], ChatResponse],
    ) -> None:
        self.project_service = project_service
        self.planner = planner
        self.tool_layer = tool_layer
        self.save_proposal = save_proposal
        self.agent_loop = ManagerAgentLoop(
            planner=self.planner,
            tool_layer=self.tool_layer,
            save_proposal=self.save_proposal,
        )
        self.graph = self._build_graph()

    def invoke(self, project_id: str, request: ChatRequest) -> ChatResponse:
        result = self.graph.invoke({"project_id": project_id, "request": request})
        response = result.get("response")
        if not response:
            raise ManagerPlanningError("Manager workflow ended without a response.")
        return response

    def _build_graph(self):
        builder = StateGraph(ManagerWorkflowState)
        builder.add_node("load_snapshot", self._load_snapshot)
        builder.add_node("agent_tool_loop", self._agent_tool_loop)

        builder.add_edge(START, "load_snapshot")
        builder.add_edge("load_snapshot", "agent_tool_loop")
        builder.add_edge("agent_tool_loop", END)
        return builder.compile()

    def _load_snapshot(self, state: ManagerWorkflowState) -> ManagerWorkflowState:
        return {"snapshot": self.project_service.get_project_snapshot(state["project_id"])}

    def _agent_tool_loop(self, state: ManagerWorkflowState) -> ManagerWorkflowState:
        return {"response": self.agent_loop.run(state["project_id"], state["snapshot"], state["request"])}
