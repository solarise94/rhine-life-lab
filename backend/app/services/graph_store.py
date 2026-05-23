from __future__ import annotations

from pathlib import Path

from app.models.cards import Card
from app.models.chat import ChatSession
from app.models.graph import Asset, Claim, GraphState, Module, ReportItem, RunRecord
from app.models.memory import ProjectMemoryItem
from app.models.patches import Proposal
from app.models.project import ProjectState
from app.models.runs import RunEvent
from app.services.utils import atomic_write_json, read_json


class GraphStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def load_project_state(self) -> ProjectState:
        return ProjectState.model_validate(read_json(self._path("project.json"), {}))

    def save_project_state(self, state: ProjectState) -> None:
        atomic_write_json(self._path("project.json"), state.model_dump())

    def load_cards(self) -> list[Card]:
        return [Card.model_validate(item) for item in read_json(self._path("graph", "cards.json"), [])]

    def save_cards(self, cards: list[Card]) -> None:
        atomic_write_json(self._path("graph", "cards.json"), [card.model_dump() for card in cards])

    def load_modules(self) -> list[Module]:
        return [Module.model_validate(item) for item in read_json(self._path("graph", "modules.json"), [])]

    def save_modules(self, modules: list[Module]) -> None:
        atomic_write_json(self._path("graph", "modules.json"), [item.model_dump() for item in modules])

    def load_assets(self) -> list[Asset]:
        return [Asset.model_validate(item) for item in read_json(self._path("graph", "assets.json"), [])]

    def save_assets(self, assets: list[Asset]) -> None:
        atomic_write_json(self._path("graph", "assets.json"), [item.model_dump() for item in assets])

    def load_claims(self) -> list[Claim]:
        return [Claim.model_validate(item) for item in read_json(self._path("graph", "claims.json"), [])]

    def save_claims(self, claims: list[Claim]) -> None:
        atomic_write_json(self._path("graph", "claims.json"), [item.model_dump() for item in claims])

    def load_runs(self) -> list[RunRecord]:
        return [RunRecord.model_validate(item) for item in read_json(self._path("graph", "runs.json"), [])]

    def save_runs(self, runs: list[RunRecord]) -> None:
        atomic_write_json(self._path("graph", "runs.json"), [item.model_dump() for item in runs])

    def load_report_items(self) -> list[ReportItem]:
        return [ReportItem.model_validate(item) for item in read_json(self._path("graph", "report.json"), [])]

    def save_report_items(self, items: list[ReportItem]) -> None:
        atomic_write_json(self._path("graph", "report.json"), [item.model_dump() for item in items])

    def load_graph(self) -> GraphState:
        metadata = read_json(self._path("graph", "graph.json"), {"schema_version": "0.1.0"})
        return GraphState(
            modules=self.load_modules(),
            assets=self.load_assets(),
            claims=self.load_claims(),
            runs=self.load_runs(),
            report_items=self.load_report_items(),
            metadata=metadata,
        )

    def save_graph(self, graph: GraphState) -> None:
        self.save_modules(graph.modules)
        self.save_assets(graph.assets)
        self.save_claims(graph.claims)
        self.save_runs(graph.runs)
        self.save_report_items(graph.report_items)
        atomic_write_json(self._path("graph", "graph.json"), graph.metadata)

    def load_proposals(self) -> list[Proposal]:
        return [Proposal.model_validate(item) for item in read_json(self._path("graph", "proposals.json"), [])]

    def save_proposals(self, proposals: list[Proposal]) -> None:
        atomic_write_json(self._path("graph", "proposals.json"), [item.model_dump() for item in proposals])

    def load_patch(self, patch_id: str) -> dict:
        return read_json(self._path("graph", "patches", f"{patch_id}.json"), {})

    def save_patch(self, patch_id: str, payload: dict) -> None:
        atomic_write_json(self._path("graph", "patches", f"{patch_id}.json"), payload)

    def load_run_events(self, run_id: str) -> list[RunEvent]:
        path = self._path("runs", run_id, "events.json")
        return [RunEvent.model_validate(item) for item in read_json(path, [])]

    def save_run_events(self, run_id: str, events: list[RunEvent]) -> None:
        atomic_write_json(self._path("runs", run_id, "events.json"), [item.model_dump() for item in events])

    def load_chat_sessions(self) -> list[ChatSession]:
        return [ChatSession.model_validate(item) for item in read_json(self._path("chat", "sessions.json"), [])]

    def save_chat_sessions(self, sessions: list[ChatSession]) -> None:
        atomic_write_json(self._path("chat", "sessions.json"), [item.model_dump() for item in sessions])

    def load_project_memory(self) -> list[ProjectMemoryItem]:
        return [ProjectMemoryItem.model_validate(item) for item in read_json(self._path("memory", "project_memory.json"), [])]

    def save_project_memory(self, items: list[ProjectMemoryItem]) -> None:
        atomic_write_json(self._path("memory", "project_memory.json"), [item.model_dump() for item in items])
