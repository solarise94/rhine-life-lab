from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from app.models.cards import Card
from app.models.graph import Asset, GraphState, RunRecord


class DependencyAttentionService:
    """Derive dependency attention issues from the current project snapshot."""

    VALID_INPUT_STATUSES = {"valid", "candidate"}
    ERROR_INPUT_STATUSES = {"rejected", "archived", "missing"}
    INACTIVE_PRODUCER_CARD_STATUSES = {"cancelled", "rejected", "superseded"}

    def analyze_project(self, snapshot: dict[str, Any], *, max_lineage_nodes: int = 200) -> dict[str, Any]:
        cards: list[Card] = list(snapshot.get("cards") or [])
        graph: GraphState = snapshot["graph"]
        indexes = self._build_indexes(cards, graph)
        issues: list[dict[str, Any]] = []

        for card in cards:
            self._analyze_card_inputs(card, indexes, issues, max_lineage_nodes=max_lineage_nodes)
            self._analyze_card_outputs(card, indexes, issues, max_lineage_nodes=max_lineage_nodes)

        issues = self._dedupe_and_sort(issues)
        return self._result(issues, cards)

    def issues_for_card(self, snapshot: dict[str, Any], card_id: str) -> list[dict[str, Any]]:
        result = self.analyze_project(snapshot)
        return list(result["issues_by_card"].get(card_id, []))

    def inspect(
        self,
        snapshot: dict[str, Any],
        *,
        card_ids: list[str] | None = None,
        source_card_id: str | None = None,
        include_recursive_downstream: bool = False,
        max_issues: int = 50,
    ) -> dict[str, Any]:
        analysis = self.analyze_project(snapshot)
        selected_card_ids: set[str] | None = None
        affected_downstream: list[dict[str, Any]] = []
        repair_order: list[str] = []

        if source_card_id and include_recursive_downstream:
            affected_downstream = self.affected_downstream(snapshot, source_card_id)
            repair_order = [item["card_id"] for item in affected_downstream]
            selected_card_ids = set(repair_order)
        elif card_ids:
            selected_card_ids = {str(item).strip() for item in card_ids if str(item).strip()}

        issues = list(analysis["issues"])
        if selected_card_ids is not None:
            issues = [issue for issue in issues if issue.get("card_id") in selected_card_ids]
        limited = issues[: max(1, min(int(max_issues or 50), 200))]

        if selected_card_ids is not None and not affected_downstream:
            issue_counts = self._issue_counts_by_card(issues)
            affected_downstream = [
                {
                    "card_id": card_id,
                    "dependency_depth": 0,
                    "issue_count": issue_counts.get(card_id, 0),
                }
                for card_id in sorted(selected_card_ids)
            ]
            repair_order = [item["card_id"] for item in affected_downstream]
        elif affected_downstream:
            issue_counts = self._issue_counts_by_card(issues)
            affected_downstream = [
                {
                    **item,
                    "issue_count": issue_counts.get(item["card_id"], 0),
                }
                for item in affected_downstream
            ]

        severity_counts: dict[str, int] = {}
        for issue in limited:
            severity = str(issue.get("severity") or "warning")
            severity_counts[severity] = severity_counts.get(severity, 0) + 1

        return {
            "issue_count": len(issues),
            "returned_issue_count": len(limited),
            "fingerprint": self._fingerprint(issues),
            "dependency_attention": limited,
            "issues_by_card": self._issues_by_card(limited),
            "severity_counts": severity_counts,
            "affected_downstream": affected_downstream,
            "repair_execution_order": repair_order,
            "truncated": len(limited) < len(issues),
        }

    def affected_downstream(self, snapshot: dict[str, Any], source_card_id: str) -> list[dict[str, Any]]:
        cards: list[Card] = list(snapshot.get("cards") or [])
        graph: GraphState = snapshot["graph"]
        producer_by_asset = self._producer_by_asset(cards, graph.assets, graph.runs)
        produced_by_card: dict[str, set[str]] = {}
        for asset_id, card_id in producer_by_asset.items():
            produced_by_card.setdefault(card_id, set()).add(asset_id)
        for card in cards:
            for output in card.outputs:
                if output.asset_id:
                    produced_by_card.setdefault(card.card_id, set()).add(output.asset_id)

        card_by_id = {card.card_id: card for card in cards}
        asset_by_id = {asset.asset_id: asset for asset in graph.assets}
        consumers_by_card: dict[str, set[str]] = {}

        for card in cards:
            for input_ref in card.inputs:
                if not input_ref.asset_id:
                    continue
                producer_card_id = producer_by_asset.get(input_ref.asset_id)
                if producer_card_id and producer_card_id != card.card_id:
                    consumers_by_card.setdefault(producer_card_id, set()).add(card.card_id)

        for asset in graph.assets:
            target_card_id = producer_by_asset.get(asset.asset_id)
            if not target_card_id:
                continue
            for upstream_asset_id in asset.depends_on:
                upstream_card_id = producer_by_asset.get(upstream_asset_id)
                if upstream_card_id and upstream_card_id != target_card_id:
                    consumers_by_card.setdefault(upstream_card_id, set()).add(target_card_id)

        queue = [(source_card_id, 0)]
        seen = {source_card_id}
        affected: list[dict[str, Any]] = []
        while queue:
            current_id, depth = queue.pop(0)
            for downstream_id in sorted(consumers_by_card.get(current_id, set())):
                if downstream_id in seen:
                    continue
                seen.add(downstream_id)
                downstream_depth = depth + 1
                reason = f"Depends on outputs from {current_id}."
                if downstream_id in card_by_id:
                    affected.append(
                        {
                            "card_id": downstream_id,
                            "dependency_depth": downstream_depth,
                            "reason": reason,
                        }
                    )
                    queue.append((downstream_id, downstream_depth))

        return sorted(affected, key=lambda item: (int(item["dependency_depth"]), str(item["card_id"])))

    def mutation_hint(self, snapshot: dict[str, Any], source_card_id: str) -> dict[str, Any]:
        affected = self.affected_downstream(snapshot, source_card_id)
        if not affected:
            return {
                "dependency_attention_check_recommended": False,
                "affected_downstream": [],
            }
        return {
            "dependency_attention_check_recommended": True,
            "affected_downstream": affected,
            "recommended_next_tool": "inspect_dependency_attention",
            "repair_execution_order_hint": [item["card_id"] for item in affected],
        }

    @staticmethod
    def attention_severity(issues: list[dict[str, Any]]) -> str | None:
        severities = {str(issue.get("severity") or "") for issue in issues}
        if "error" in severities:
            return "error"
        if "warning" in severities:
            return "warning"
        if "info" in severities:
            return "info"
        return None

    def _build_indexes(self, cards: list[Card], graph: GraphState) -> dict[str, Any]:
        card_by_id = {card.card_id: card for card in cards}
        asset_by_id = {asset.asset_id: asset for asset in graph.assets}
        run_by_id = {run.run_id: run for run in graph.runs}
        run_card_by_id = {run.run_id: run.card_id for run in graph.runs}

        planned_output_by_asset_id: dict[str, dict[str, Any]] = {}
        current_output_by_card_role: dict[tuple[str, str], Any] = {}
        for card in cards:
            for output in card.outputs:
                if not output.asset_id:
                    continue
                planned_output_by_asset_id[output.asset_id] = {
                    "card_id": card.card_id,
                    "role": output.role,
                    "output": output,
                }
                if output.role:
                    current_output_by_card_role[(card.card_id, output.role)] = output

        producer_card_by_asset: dict[str, str] = {}
        role_by_asset: dict[str, str] = {}
        for asset in graph.assets:
            producer_card_id = run_card_by_id.get(asset.created_by_run or "")
            if producer_card_id:
                producer_card_by_asset[asset.asset_id] = producer_card_id
            role = str(asset.metadata.get("role") or "").strip()
            if role:
                role_by_asset[asset.asset_id] = role

        return {
            "card_by_id": card_by_id,
            "asset_by_id": asset_by_id,
            "run_by_id": run_by_id,
            "run_card_by_id": run_card_by_id,
            "planned_output_by_asset_id": planned_output_by_asset_id,
            "current_output_by_card_role": current_output_by_card_role,
            "producer_card_by_asset": producer_card_by_asset,
            "role_by_asset": role_by_asset,
        }

    def _analyze_card_inputs(
        self,
        card: Card,
        indexes: dict[str, Any],
        issues: list[dict[str, Any]],
        *,
        max_lineage_nodes: int,
    ) -> None:
        asset_by_id: dict[str, Asset] = indexes["asset_by_id"]
        planned_output_by_asset_id = indexes["planned_output_by_asset_id"]
        producer_card_by_asset = indexes["producer_card_by_asset"]
        role_by_asset = indexes["role_by_asset"]
        card_by_id: dict[str, Card] = indexes["card_by_id"]
        current_output_by_card_role = indexes["current_output_by_card_role"]

        for input_ref in card.inputs:
            asset_id = input_ref.asset_id
            if not asset_id:
                continue
            asset = asset_by_id.get(asset_id)
            if not asset:
                if asset_id not in planned_output_by_asset_id:
                    issues.append(
                        self._issue(
                            kind="input_asset_missing",
                            severity="error",
                            card=card,
                            asset_id=asset_id,
                            label=input_ref.label,
                            message=f"Input asset {input_ref.label or asset_id} is missing.",
                        )
                    )
                continue

            if asset.status not in self.VALID_INPUT_STATUSES:
                issues.append(
                    self._issue(
                        kind="input_asset_not_valid",
                        severity="error" if asset.status in self.ERROR_INPUT_STATUSES else "warning",
                        card=card,
                        asset_id=asset.asset_id,
                        asset_status=asset.status,
                        label=input_ref.label,
                        message=f"Input asset {input_ref.label or asset.asset_id} has status {asset.status}.",
                    )
                )
                continue
            if asset.status == "candidate":
                continue

            producer_card_id = producer_card_by_asset.get(asset.asset_id)
            role = role_by_asset.get(asset.asset_id)
            if producer_card_id:
                producer_card = card_by_id.get(producer_card_id)
                if producer_card and producer_card.status in self.INACTIVE_PRODUCER_CARD_STATUSES:
                    issues.append(
                        self._issue(
                            kind="input_producer_card_inactive",
                            severity="warning",
                            card=card,
                            asset_id=asset.asset_id,
                            asset_status=asset.status,
                            label=input_ref.label,
                            producer_card_id=producer_card.card_id,
                            producer_card_status=producer_card.status,
                            message=f"Input asset {input_ref.label or asset.asset_id} comes from inactive card {producer_card.card_id}.",
                        )
                    )
                if producer_card and role:
                    producer_roles = {output.role for output in producer_card.outputs if output.role}
                    if role not in producer_roles:
                        issues.append(
                            self._issue(
                                kind="input_producer_output_removed",
                                severity="warning",
                                card=card,
                                asset_id=asset.asset_id,
                                asset_status=asset.status,
                                label=input_ref.label,
                                producer_card_id=producer_card_id,
                                producer_role=role,
                                message=f"Input asset {input_ref.label or asset.asset_id} uses removed output role {role}.",
                            )
                        )
                    current_output = current_output_by_card_role.get((producer_card_id, role))
                    current_asset = asset_by_id.get(current_output.asset_id) if current_output and current_output.asset_id else None
                    if current_asset and current_asset.asset_id != asset.asset_id and current_asset.status in self.VALID_INPUT_STATUSES:
                        issues.append(
                            self._issue(
                                kind="input_asset_outdated",
                                severity="warning",
                                card=card,
                                asset_id=asset.asset_id,
                                asset_status=asset.status,
                                label=input_ref.label,
                                producer_card_id=producer_card_id,
                                producer_role=role,
                                current_asset_id=current_asset.asset_id,
                                message=f"Input asset {input_ref.label or asset.asset_id} references an older {producer_card_id} output for role {role}.",
                            )
                        )

            invalid_roots, truncated = self._find_invalid_lineage_roots(asset.asset_id, asset_by_id, max_nodes=max_lineage_nodes)
            if invalid_roots:
                issues.append(
                    self._issue(
                        kind="asset_lineage_invalid",
                        severity="warning",
                        card=card,
                        asset_id=asset.asset_id,
                        asset_status=asset.status,
                        label=input_ref.label,
                        upstream_invalid_assets=invalid_roots[:8],
                        truncated=truncated,
                        message=f"Input asset {input_ref.label or asset.asset_id} has invalid upstream lineage.",
                    )
                )

    def _analyze_card_outputs(
        self,
        card: Card,
        indexes: dict[str, Any],
        issues: list[dict[str, Any]],
        *,
        max_lineage_nodes: int,
    ) -> None:
        if card.status != "accepted":
            return
        asset_by_id: dict[str, Asset] = indexes["asset_by_id"]
        for output in card.outputs:
            if not output.asset_id:
                issues.append(
                    self._issue(
                        kind="output_asset_not_valid",
                        severity="error",
                        card=card,
                        asset_status="missing",
                        producer_role=output.role,
                        message=f"Accepted card output {output.role or output.label} has no asset_id.",
                    )
                )
                continue
            asset = asset_by_id.get(output.asset_id)
            if not asset:
                issues.append(
                    self._issue(
                        kind="output_asset_not_valid",
                        severity="error",
                        card=card,
                        asset_id=output.asset_id,
                        producer_role=output.role,
                        message=f"Accepted card output {output.role or output.label} points to missing asset {output.asset_id}.",
                    )
                )
                continue
            if asset.status != "valid":
                issues.append(
                    self._issue(
                        kind="output_asset_not_valid",
                        severity="warning" if asset.status == "candidate" else "error",
                        card=card,
                        asset_id=asset.asset_id,
                        asset_status=asset.status,
                        producer_role=output.role,
                        message=f"Accepted card output {output.role or output.label} points to {asset.status} asset {asset.asset_id}.",
                    )
                )
                continue
            invalid_roots, truncated = self._find_invalid_lineage_roots(asset.asset_id, asset_by_id, max_nodes=max_lineage_nodes)
            if invalid_roots:
                issues.append(
                    self._issue(
                        kind="asset_lineage_invalid",
                        severity="warning",
                        card=card,
                        asset_id=asset.asset_id,
                        asset_status=asset.status,
                        producer_role=output.role,
                        upstream_invalid_assets=invalid_roots[:8],
                        truncated=truncated,
                        message=f"Output asset {output.role or asset.asset_id} has invalid upstream lineage.",
                    )
                )

    def _find_invalid_lineage_roots(
        self,
        start_asset_id: str,
        asset_by_id: dict[str, Asset],
        *,
        max_nodes: int,
    ) -> tuple[list[dict[str, str]], bool]:
        start = asset_by_id.get(start_asset_id)
        if not start:
            return [], False
        invalid: list[dict[str, str]] = []
        seen: set[str] = set()
        queue = list(start.depends_on)
        truncated = False
        while queue:
            if len(seen) >= max_nodes:
                truncated = True
                break
            upstream_id = queue.pop(0)
            if upstream_id in seen:
                continue
            seen.add(upstream_id)
            upstream = asset_by_id.get(upstream_id)
            if upstream is None:
                invalid.append({"asset_id": upstream_id, "status": "missing"})
                continue
            if upstream.status not in self.VALID_INPUT_STATUSES:
                invalid.append({"asset_id": upstream.asset_id, "status": upstream.status})
                continue
            queue.extend(upstream.depends_on)
        return invalid, truncated

    def _issue(self, *, kind: str, severity: str, card: Card, **kwargs: Any) -> dict[str, Any]:
        issue = {
            "kind": kind,
            "severity": severity,
            "card_id": card.card_id,
            "card_title": card.title,
            **{key: value for key, value in kwargs.items() if value is not None},
        }
        issue["issue_id"] = self._issue_id(issue)
        if "suggested_actions" not in issue:
            issue["suggested_actions"] = [
                "检查下游结果是否仍可信",
                "必要时重跑该 card",
                "若用户确认沿用旧结果，可忽略该 attention",
            ]
        return issue

    def _dedupe_and_sort(self, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: dict[str, dict[str, Any]] = {}
        for issue in issues:
            deduped.setdefault(str(issue["issue_id"]), issue)
        severity_rank = {"error": 0, "warning": 1, "info": 2}
        return sorted(
            deduped.values(),
            key=lambda item: (
                severity_rank.get(str(item.get("severity")), 9),
                str(item.get("card_id") or ""),
                str(item.get("kind") or ""),
                str(item.get("asset_id") or ""),
                str(item.get("current_asset_id") or ""),
            ),
        )

    def _result(self, issues: list[dict[str, Any]], cards: list[Card]) -> dict[str, Any]:
        severity_counts: dict[str, int] = {}
        for issue in issues:
            severity = str(issue.get("severity") or "warning")
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
        return {
            "issue_count": len(issues),
            "fingerprint": self._fingerprint(issues),
            "issues": issues,
            "issues_by_card": self._issues_by_card(issues),
            "severity_counts": severity_counts,
            "card_count": len(cards),
        }

    @staticmethod
    def _issues_by_card(issues: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for issue in issues:
            card_id = issue.get("card_id")
            if card_id:
                grouped.setdefault(str(card_id), []).append(issue)
        return grouped

    @staticmethod
    def _issue_counts_by_card(issues: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in issues:
            card_id = str(issue.get("card_id") or "")
            if card_id:
                counts[card_id] = counts.get(card_id, 0) + 1
        return counts

    @staticmethod
    def _issue_id(issue: dict[str, Any]) -> str:
        parts = [
            issue.get("kind") or "-",
            issue.get("card_id") or "-",
            issue.get("asset_id") or "-",
            issue.get("current_asset_id") or "-",
            issue.get("producer_role") or "-",
        ]
        return ":".join(str(part) for part in parts)

    def _fingerprint(self, issues: list[dict[str, Any]]) -> str:
        stable_parts = [
            (
                issue.get("kind"),
                issue.get("card_id"),
                issue.get("asset_id"),
                issue.get("asset_status"),
                issue.get("current_asset_id"),
                issue.get("producer_card_id"),
                issue.get("producer_role"),
            )
            for issue in sorted(issues, key=lambda item: str(item.get("issue_id") or ""))
        ]
        return sha256(json.dumps(stable_parts, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def _producer_by_asset(cards: list[Card], assets: list[Asset], runs: list[RunRecord]) -> dict[str, str]:
        run_card_by_id = {run.run_id: run.card_id for run in runs}
        producer_by_asset: dict[str, str] = {}
        for asset in assets:
            producer_card_id = run_card_by_id.get(asset.created_by_run or "")
            if producer_card_id:
                producer_by_asset[asset.asset_id] = producer_card_id
        for card in cards:
            for output in card.outputs:
                if output.asset_id:
                    producer_by_asset[output.asset_id] = card.card_id
        return producer_by_asset
