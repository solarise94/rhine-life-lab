from __future__ import annotations

from app.models.graph import Asset, GraphState
from app.services.utils import utc_now


def _materializations(graph: GraphState) -> dict[str, dict]:
    if not isinstance(graph.metadata, dict):
        graph.metadata = {}
    if "asset_materializations" not in graph.metadata:
        graph.metadata["asset_materializations"] = {}
    return graph.metadata["asset_materializations"]


class AssetMaterializationService:
    """Manages the logical -> concrete asset binding layer stored in GraphState.metadata.

    The binding map lives at graph.metadata["asset_materializations"] and maps a
    planned/logical asset_id to its current concrete materialization.
    """

    @staticmethod
    def current_for_logical(graph: GraphState, planned_asset_id: str) -> dict | None:
        """Return the current materialization binding for a logical asset id.

        Returns None if no binding exists or the binding has no current_asset_id.
        """
        if not planned_asset_id:
            return None
        mats = _materializations(graph)
        binding = mats.get(planned_asset_id)
        if not binding:
            return None
        current_asset_id = binding.get("current_asset_id")
        if not current_asset_id:
            return None
        return binding

    @staticmethod
    def set_current(
        graph: GraphState,
        planned_asset_id: str,
        concrete_asset: Asset,
        producer_card_id: str,
        producer_role: str,
        producer_run_id: str,
    ) -> None:
        """Write or update the binding for a logical asset id.

        If a previous concrete asset was bound, its id is moved to
        superseded_asset_ids. All writes happen in-place on graph.metadata.
        """
        if not planned_asset_id:
            return
        mats = _materializations(graph)
        now = utc_now()
        existing = mats.get(planned_asset_id)
        superseded: list[str] = []
        if existing:
            prev = existing.get("current_asset_id")
            if prev and prev != concrete_asset.asset_id:
                superseded = list(existing.get("superseded_asset_ids") or [])
                if prev not in superseded:
                    superseded.append(prev)

        mats[planned_asset_id] = {
            "planned_asset_id": planned_asset_id,
            "current_asset_id": concrete_asset.asset_id,
            "producer_card_id": producer_card_id,
            "producer_role": producer_role,
            "producer_run_id": producer_run_id,
            "status": concrete_asset.status,
            "path": concrete_asset.path,
            "updated_at": now,
            "superseded_asset_ids": superseded,
        }

    @staticmethod
    def bootstrap_from_aliases(graph: GraphState, cards: list) -> None:
        """For legacy projects: build binding map from Asset.metadata['planned_asset_id'].

        Only populates bindings that are missing. Does not overwrite existing
        explicit bindings. Should be called lazily when graph is loaded.
        """
        from app.models.cards import Card

        mats = _materializations(graph)
        asset_by_id = {asset.asset_id: asset for asset in graph.assets}
        run_order_by_id = {run.run_id: index for index, run in enumerate(graph.runs)}

        # Build reverse map: planned_asset_id -> list of concrete assets
        alias_assets: dict[str, list[Asset]] = {}
        for asset in graph.assets:
            metadata = asset.metadata if isinstance(asset.metadata, dict) else {}
            planned = str(metadata.get("planned_asset_id") or "").strip()
            if planned:
                alias_assets.setdefault(planned, []).append(asset)

        for card in cards:
            if not isinstance(card, Card):
                continue
            for output in card.outputs:
                if not output.asset_id or not output.role:
                    continue
                # Skip if already has an explicit binding
                if output.asset_id in mats:
                    continue
                # If output.asset_id points to a concrete asset, recover its alias
                direct = asset_by_id.get(output.asset_id)
                if direct:
                    metadata = direct.metadata if isinstance(direct.metadata, dict) else {}
                    planned = str(metadata.get("planned_asset_id") or "").strip()
                    if planned and planned in alias_assets:
                        # Find the best candidate for this planned id
                        candidates = alias_assets[planned]
                        best = _pick_best_candidate(candidates, run_order_by_id=run_order_by_id)
                        if best:
                            mats[planned] = {
                                "planned_asset_id": planned,
                                "current_asset_id": best.asset_id,
                                "producer_card_id": card.card_id,
                                "producer_role": output.role,
                                "producer_run_id": best.created_by_run or "",
                                "status": best.status,
                                "path": best.path,
                                "updated_at": utc_now(),
                                "superseded_asset_ids": [],
                            }
                else:
                    # output.asset_id is a logical id with alias candidates
                    candidates = alias_assets.get(output.asset_id, [])
                    if candidates:
                        best = _pick_best_candidate(candidates, run_order_by_id=run_order_by_id)
                        if best:
                            mats[output.asset_id] = {
                                "planned_asset_id": output.asset_id,
                                "current_asset_id": best.asset_id,
                                "producer_card_id": card.card_id,
                                "producer_role": output.role,
                                "producer_run_id": best.created_by_run or "",
                                "status": best.status,
                                "path": best.path,
                                "updated_at": utc_now(),
                                "superseded_asset_ids": [],
                            }

    @staticmethod
    def resolve_logical_output(
        graph: GraphState,
        cards: list,
        card_id: str,
        role_or_planned_asset_id: str,
    ) -> Asset | None:
        """Resolve a logical output to its current concrete asset.

        Looks up by planned_asset_id in the binding map first, then falls back
        to finding the concrete asset directly in graph.assets.
        """
        binding = AssetMaterializationService.current_for_logical(graph, role_or_planned_asset_id)
        if binding:
            current_id = binding.get("current_asset_id")
            if current_id:
                for asset in graph.assets:
                    if asset.asset_id == current_id:
                        return asset
        # Fallback: find any asset whose metadata planned_asset_id matches
        for asset in graph.assets:
            metadata = asset.metadata if isinstance(asset.metadata, dict) else {}
            planned = str(metadata.get("planned_asset_id") or "").strip()
            if planned == role_or_planned_asset_id:
                return asset
        return None


def _pick_best_candidate(candidates: list[Asset], *, run_order_by_id: dict[str, int] | None = None) -> Asset | None:
    """Pick the best candidate from a list of alias assets.

    Prefers valid status, then newest run order.
    """
    if not candidates:
        return None
    status_rank = {"valid": 0, "candidate": 1, "stale": 2, "superseded": 3, "rejected": 4, "archived": 5, "missing": 6}
    run_order = run_order_by_id or {}

    def sort_key(asset: Asset) -> tuple[int, int]:
        return (
            status_rank.get(asset.status, 99),
            -(run_order.get(asset.created_by_run or "", -1)),  # newest run first
        )

    return sorted(candidates, key=sort_key)[0]
