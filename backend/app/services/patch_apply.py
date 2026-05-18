from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from app.models.cards import Card, CardAssetRef
from app.models.graph import Asset, Claim, GraphState, Module, ModuleRef, ReportItem, RunRecord
from app.models.patches import ApplyResult, GraphPatch
from app.services.patch_validator import PatchValidator
from app.services.project_service import ProjectService
from app.services.utils import atomic_write_json, read_json, utc_now


class PatchApplyService:
    def __init__(self, project_service: ProjectService, validator: PatchValidator) -> None:
        self.project_service = project_service
        self.validator = validator

    def apply_patch(self, project_id: str, patch: GraphPatch, actor: str = "manager_ai") -> ApplyResult:
        validation = self.validator.validate_patch(project_id, patch)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors))
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            project = store.load_project_state()
            cards = deepcopy(store.load_cards())
            graph = deepcopy(store.load_graph())
            cleanup = list(read_json(store.root / "graph" / "cleanup.json", []))
            backups = self._snapshot_files(store.root)

            try:
                for op in patch.ops:
                    payload = op.payload
                    if op.op == "create_module":
                        graph.modules.append(
                            Module(
                                module_id=payload["module_id"],
                                title=payload["title"],
                                type="analysis_module",
                                status=payload.get("status", "planned"),
                                summary=payload.get("summary", ""),
                                depends_on_assets=payload.get("depends_on_assets", []),
                                expected_outputs=payload.get("expected_outputs", []),
                                linked_cards=payload.get("linked_cards", []),
                                linked_runs=[],
                                created_by=actor,
                                created_at=utc_now(),
                            )
                        )
                    elif op.op == "create_module_group":
                        graph.modules.append(
                            Module(
                                module_id=payload["module_id"],
                                title=payload["title"],
                                type="module_group",
                                status=payload.get("status", "planned"),
                                summary=payload.get("summary", ""),
                                depends_on_assets=payload.get("depends_on_assets", []),
                                expected_outputs=payload.get("expected_outputs", []),
                                linked_cards=payload.get("linked_cards", []),
                                linked_runs=[],
                                submodules=[],
                                created_by=actor,
                                created_at=utc_now(),
                            )
                        )
                    elif op.op == "update_module_summary":
                        module = next(item for item in graph.modules if item.module_id == payload["module_id"])
                        module.summary = payload["summary"]
                    elif op.op == "add_submodule":
                        module = next(item for item in graph.modules if item.module_id == payload["parent_module_id"])
                        module.submodules.append(
                            ModuleRef(
                                module_id=payload["module_id"],
                                title=payload["title"],
                                status=payload.get("status", "planned"),
                            )
                        )
                    elif op.op == "create_card":
                        cards.append(Card.model_validate(payload))
                    elif op.op == "update_card":
                        card = next(item for item in cards if item.card_id == payload["card_id"])
                        for key, value in payload.items():
                            if key != "card_id":
                                setattr(card, key, value)
                    elif op.op == "set_card_status":
                        card = next(item for item in cards if item.card_id == payload["card_id"])
                        card.status = payload["status"]
                    elif op.op == "set_module_status":
                        module = next(item for item in graph.modules if item.module_id == payload["module_id"])
                        module.status = payload["status"]
                    elif op.op == "create_asset":
                        graph.assets.append(Asset.model_validate(payload))
                    elif op.op == "set_asset_status":
                        asset = next(item for item in graph.assets if item.asset_id == payload["asset_id"])
                        asset.status = payload["status"]
                    elif op.op == "connect_dependency":
                        asset_id = payload.get("asset_id") or payload.get("from_asset_id")
                        depends_on_asset_id = payload.get("depends_on_asset_id") or payload.get("to_asset_id")
                        asset = next(item for item in graph.assets if item.asset_id == asset_id)
                        if depends_on_asset_id not in asset.depends_on:
                            asset.depends_on.append(depends_on_asset_id)
                    elif op.op == "create_claim":
                        graph.claims.append(Claim.model_validate(payload))
                    elif op.op == "set_claim_status":
                        claim = next(item for item in graph.claims if item.claim_id == payload["claim_id"])
                        claim.status = payload["status"]
                    elif op.op == "attach_asset_to_card":
                        card = next(item for item in cards if item.card_id == payload["card_id"])
                        if payload["asset_id"] not in card.linked_assets:
                            card.linked_assets.append(payload["asset_id"])
                        card.outputs.append(CardAssetRef(label=payload["label"], asset_id=payload["asset_id"]))
                    elif op.op == "create_run":
                        graph.runs.append(RunRecord.model_validate(payload))
                    elif op.op == "attach_run_to_card":
                        card = next(item for item in cards if item.card_id == payload["card_id"])
                        if payload["run_id"] not in card.linked_runs:
                            card.linked_runs.append(payload["run_id"])
                    elif op.op == "add_report_item":
                        graph.report_items.append(ReportItem.model_validate(payload))
                    elif op.op == "remove_report_item":
                        graph.report_items = [item for item in graph.report_items if item.item_id != payload["item_id"]]
                    elif op.op == "mark_downstream_stale":
                        self._mark_assets_stale(graph, payload.get("asset_ids", []))
                    elif op.op == "propose_cleanup":
                        cleanup.append(
                            {
                                "cleanup_id": payload.get("cleanup_id", f"cleanup_{len(cleanup) + 1:03d}"),
                                "message": payload.get("message", ""),
                                "asset_ids": payload.get("asset_ids", []),
                                "created_at": utc_now(),
                            }
                        )
                    elif op.op == "semantic_rollback":
                        self._apply_semantic_rollback(graph, cards, cleanup, payload)

                for card in cards:
                    if card.card_type == "module_group":
                        self._recompute_group_status(card, graph.modules)

                GraphState.model_validate(graph.model_dump())
                [Card.model_validate(card.model_dump()) for card in cards]
                project.updated_at = utc_now()
                store.save_graph(graph)
                store.save_cards(cards)
                store.save_project_state(project)
                atomic_write_json(store.root / "graph" / "cleanup.json", cleanup)
                commit_hash = self.project_service.git_service(project_id).commit(f"Apply patch {patch.patch_id}")
                return ApplyResult(project_id=project_id, patch_id=patch.patch_id, commit_hash=commit_hash, warnings=validation.warnings)
            except Exception as exc:
                restore_failed = not self._restore_snapshot(backups)
                if restore_failed:
                    project.status = "error"
                    project.updated_at = utc_now()
                    store.save_project_state(project)
                raise RuntimeError("Patch apply failed and project state was restored." if not restore_failed else "Patch apply failed and project recovery is required.") from exc

    @staticmethod
    def _mark_assets_stale(graph: GraphState, asset_ids: list[str]) -> None:
        targets = set(asset_ids)
        for asset in graph.assets:
            if asset.asset_id in targets and asset.status == "valid":
                asset.status = "stale"
        for claim in graph.claims:
            if targets.intersection(claim.depends_on_assets) and claim.status == "valid":
                claim.status = "stale"

    @staticmethod
    def _apply_semantic_rollback(graph: GraphState, cards: list[Card], cleanup: list[dict], payload: dict) -> None:
        target_asset_ids = set(payload.get("target_asset_ids", []))
        target_run_ids = set(payload.get("target_run_ids", []))
        target_card_ids = set(payload.get("target_card_ids", []))
        reason = payload.get("message") or payload.get("reason") or "Semantic rollback"

        if target_run_ids:
            target_asset_ids.update(asset.asset_id for asset in graph.assets if asset.created_by_run in target_run_ids)
            target_card_ids.update(run.card_id for run in graph.runs if run.run_id in target_run_ids)
        if target_card_ids:
            for card in cards:
                if card.card_id in target_card_ids:
                    target_asset_ids.update(card.linked_assets)

        stale_asset_ids: set[str] = set()
        superseded_asset_ids: set[str] = set()
        changed = True
        while changed:
            changed = False
            for asset in graph.assets:
                if asset.asset_id in target_asset_ids and asset.asset_id not in stale_asset_ids:
                    asset.status = "stale"
                    stale_asset_ids.add(asset.asset_id)
                    changed = True
                elif set(asset.depends_on).intersection(stale_asset_ids) and asset.asset_id not in superseded_asset_ids:
                    if asset.status == "valid":
                        asset.status = "superseded"
                    superseded_asset_ids.add(asset.asset_id)
                    changed = True

        affected_asset_ids = stale_asset_ids | superseded_asset_ids
        for claim in graph.claims:
            if affected_asset_ids.intersection(claim.depends_on_assets):
                claim.status = "stale" if set(claim.depends_on_assets).intersection(stale_asset_ids) else "superseded"
        for card in cards:
            if set(card.linked_assets).intersection(affected_asset_ids):
                card.status = "stale" if set(card.linked_assets).intersection(stale_asset_ids) else "superseded"
                card.progress_note = None
                card.manager_review = reason
        for module in graph.modules:
            if set(module.depends_on_assets).intersection(affected_asset_ids):
                module.status = "stale" if set(module.depends_on_assets).intersection(stale_asset_ids) else "superseded"

        cleanup.append(
            {
                "cleanup_id": payload.get("cleanup_id", f"cleanup_{len(cleanup) + 1:03d}"),
                "message": reason,
                "stale_asset_ids": sorted(stale_asset_ids),
                "superseded_asset_ids": sorted(superseded_asset_ids),
                "created_at": utc_now(),
            }
        )

    @staticmethod
    def _snapshot_files(project_root: Path) -> dict[Path, bytes | None]:
        paths = [
            project_root / "project.json",
            project_root / "graph" / "cards.json",
            project_root / "graph" / "modules.json",
            project_root / "graph" / "assets.json",
            project_root / "graph" / "claims.json",
            project_root / "graph" / "runs.json",
            project_root / "graph" / "report.json",
            project_root / "graph" / "graph.json",
            project_root / "graph" / "cleanup.json",
        ]
        snapshot: dict[Path, bytes | None] = {}
        for path in paths:
            snapshot[path] = path.read_bytes() if path.exists() else None
        return snapshot

    @staticmethod
    def _restore_snapshot(snapshot: dict[Path, bytes | None]) -> bool:
        try:
            for path, payload in snapshot.items():
                if payload is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payload)
            return True
        except Exception:
            return False

    @staticmethod
    def _recompute_group_status(card: Card, modules: list[Module]) -> None:
        group = next((item for item in modules if item.module_id in card.linked_modules), None)
        if not group or not group.submodules:
            return
        child_statuses = [item.status for item in group.submodules]
        if all(status == "accepted" for status in child_statuses):
            card.status = "accepted"
            card.aggregate_status = "all_accepted"
        elif any(status == "running" for status in child_statuses):
            card.status = "running"
            card.aggregate_status = "has_running"
        elif any(status == "failed" for status in child_statuses):
            card.status = "failed"
            card.aggregate_status = "has_failed"
        elif any(status == "stale" for status in child_statuses):
            card.status = "stale"
            card.aggregate_status = "stale"
        elif any(status == "planned" for status in child_statuses):
            card.status = "planned"
            card.aggregate_status = "partially_planned"
        else:
            card.aggregate_status = "mixed"
