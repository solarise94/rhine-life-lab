import unittest

from app.models.cards import Card
from app.models.graph import Asset, GraphState, RunRecord
from app.models.output_contracts import CardOutputSpec
from app.services.dependency_attention_service import DependencyAttentionService
from app.services.input_resolution_service import InputResolutionService


def output(role: str, asset_id: str | None = None) -> CardOutputSpec:
    return CardOutputSpec(
        role=role,
        label=role,
        artifact_class="table",
        accepted_formats=["tsv"],
        preferred_format="tsv",
        asset_id=asset_id,
    )


def card(card_id: str, *, status: str = "planned", inputs: list[dict] | None = None, outputs: list[CardOutputSpec] | None = None) -> Card:
    return Card(
        card_id=card_id,
        card_type="module",
        title=card_id,
        status=status,
        step=1,
        summary=card_id,
        inputs=inputs or [],
        outputs=outputs or [],
    )


def asset(asset_id: str, *, status: str = "valid", run_id: str | None = None, role: str | None = None, depends_on: list[str] | None = None) -> Asset:
    metadata = {"role": role} if role else {}
    return Asset(
        asset_id=asset_id,
        asset_type="table",
        title=asset_id,
        status=status,
        created_by_run=run_id,
        path=f"results/{asset_id}.tsv",
        summary=asset_id,
        depends_on=depends_on or [],
        metadata=metadata,
    )


def run(run_id: str, card_id: str) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        card_id=card_id,
        status="reviewed",
        title=run_id,
        summary=run_id,
        started_at="2026-05-28T00:00:00Z",
    )


class DependencyAttentionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DependencyAttentionService()
        self.input_resolution_service = InputResolutionService()

    def _snapshot(self, cards: list[Card], assets: list[Asset], runs: list[RunRecord]) -> dict:
        return {"cards": cards, "graph": GraphState(assets=assets, runs=runs)}

    def _kinds(self, snapshot: dict) -> set[str]:
        return {issue["kind"] for issue in self.service.analyze_project(snapshot)["issues"]}

    def test_input_asset_missing(self) -> None:
        downstream = card("downstream", status="accepted", inputs=[{"label": "missing", "asset_id": "missing_asset"}])
        self.assertIn("input_asset_missing", self._kinds(self._snapshot([downstream], [], [])))

    def test_planned_card_does_not_emit_input_attention(self) -> None:
        downstream = card("downstream", status="planned", inputs=[{"label": "missing", "asset_id": "missing_asset"}])
        self.assertEqual(set(), self._kinds(self._snapshot([downstream], [], [])))

    def test_input_asset_not_valid(self) -> None:
        downstream = card("downstream", status="accepted", inputs=[{"label": "old", "asset_id": "old_asset"}])
        snapshot = self._snapshot([downstream], [asset("old_asset", status="superseded")], [])
        issues = self.service.analyze_project(snapshot)["issues"]
        self.assertEqual("input_asset_not_valid", issues[0]["kind"])
        self.assertEqual("warning", issues[0]["severity"])

    def test_candidate_input_does_not_warn(self) -> None:
        downstream = card("downstream", inputs=[{"label": "candidate", "asset_id": "candidate_asset"}])
        snapshot = self._snapshot([downstream], [asset("candidate_asset", status="candidate")], [])
        self.assertEqual([], self.service.analyze_project(snapshot)["issues"])

    def test_input_asset_outdated_uses_metadata_role(self) -> None:
        producer = card("producer", status="accepted", outputs=[output("table", "new_asset")])
        downstream = card("downstream", status="accepted", inputs=[{"label": "table", "asset_id": "old_asset"}])
        snapshot = self._snapshot(
            [producer, downstream],
            [
                asset("old_asset", run_id="run_old", role="table"),
                asset("new_asset", run_id="run_new", role="table"),
            ],
            [run("run_old", "producer"), run("run_new", "producer")],
        )
        issues = self.service.analyze_project(snapshot)["issues"]
        self.assertEqual(["input_asset_outdated"], [issue["kind"] for issue in issues])
        self.assertEqual("new_asset", issues[0]["current_asset_id"])

    def test_input_asset_alias_resolves_planned_asset_id(self) -> None:
        producer = card("producer", status="accepted", outputs=[output("table", "new_asset")])
        downstream = card("downstream", status="accepted", inputs=[{"label": "table", "asset_id": "planned_table"}])
        snapshot = self._snapshot(
            [producer, downstream],
            [asset("new_asset", run_id="run_new", role="table")],
            [run("run_new", "producer")],
        )
        snapshot["graph"].assets[0].metadata["planned_asset_id"] = "planned_table"
        self.assertEqual([], self.service.analyze_project(snapshot)["issues"])

    def test_virtual_input_issue_reports_requested_and_resolved_ids(self) -> None:
        producer = card("producer", status="cancelled", outputs=[output("table", "new_asset")])
        downstream = card("downstream", status="accepted", inputs=[{"label": "table", "asset_id": "planned_table"}])
        snapshot = self._snapshot(
            [producer, downstream],
            [asset("new_asset", run_id="run_new", role="table")],
            [run("run_new", "producer")],
        )
        snapshot["graph"].assets[0].metadata["planned_asset_id"] = "planned_table"
        issues = self.service.analyze_project(snapshot)["issues"]
        self.assertEqual(["input_producer_card_inactive"], [issue["kind"] for issue in issues])
        self.assertEqual("planned_table", issues[0]["requested_asset_id"])
        self.assertEqual("new_asset", issues[0]["resolved_asset_id"])
        self.assertEqual("planned_asset_alias", issues[0]["resolved_by"])

    def test_virtual_input_without_materialized_asset_has_no_resolved_by(self) -> None:
        producer = card("producer", status="accepted", outputs=[output("table", "planned_table")])
        downstream = card("downstream", status="accepted", inputs=[{"label": "table", "asset_id": "planned_table"}])
        snapshot = self._snapshot([producer, downstream], [], [])
        index = self.input_resolution_service.build_index(snapshot["cards"], snapshot["graph"])

        resolution = self.input_resolution_service.resolve_input("planned_table", index)

        self.assertTrue(resolution.is_virtual)
        self.assertEqual("missing", resolution.status)
        self.assertIsNone(resolution.resolved_asset_id)
        self.assertIsNone(resolution.resolved_by)
        self.assertEqual("producer", resolution.producer_card_id)

    def test_inactive_producer_card_warns_without_rebinding(self) -> None:
        producer = card("producer", status="cancelled", outputs=[output("table", "old_asset")])
        downstream = card("downstream", status="accepted", inputs=[{"label": "table", "asset_id": "old_asset"}])
        snapshot = self._snapshot([producer, downstream], [asset("old_asset", run_id="run_old", role="table")], [run("run_old", "producer")])
        self.assertIn("input_producer_card_inactive", self._kinds(snapshot))

    def test_producer_output_removed_warns(self) -> None:
        producer = card("producer", status="accepted", outputs=[output("other", "other_asset")])
        downstream = card("downstream", status="accepted", inputs=[{"label": "table", "asset_id": "old_asset"}])
        snapshot = self._snapshot([producer, downstream], [asset("old_asset", run_id="run_old", role="table")], [run("run_old", "producer")])
        self.assertIn("input_producer_output_removed", self._kinds(snapshot))

    def test_accepted_output_candidate_warns(self) -> None:
        accepted = card("accepted", status="accepted", outputs=[output("report", "candidate_report")])
        snapshot = self._snapshot([accepted], [asset("candidate_report", status="candidate")], [])
        issues = self.service.analyze_project(snapshot)["issues"]
        self.assertEqual("output_asset_not_valid", issues[0]["kind"])
        self.assertEqual("warning", issues[0]["severity"])

    def test_system_output_candidate_does_not_warn(self) -> None:
        accepted = card(
            "accepted",
            status="accepted",
            outputs=[
                output("report", "valid_report"),
                output("run_summary", "candidate_summary"),
                output("rna_pca_run_preview", "candidate_preview"),
            ],
        )
        snapshot = self._snapshot(
            [accepted],
            [
                asset("valid_report", status="valid", role="report"),
                asset("candidate_summary", status="candidate", role="run_summary"),
                asset("candidate_preview", status="candidate", role="rna_pca_run_preview"),
            ],
            [],
        )
        self.assertEqual([], self.service.analyze_project(snapshot)["issues"])

    def test_local_lineage_dfs_warns_for_invalid_upstream(self) -> None:
        downstream = card("downstream", status="accepted", inputs=[{"label": "derived", "asset_id": "derived_asset"}])
        snapshot = self._snapshot(
            [downstream],
            [
                asset("root_asset", status="superseded"),
                asset("mid_asset", depends_on=["root_asset"]),
                asset("derived_asset", depends_on=["mid_asset"]),
            ],
            [],
        )
        issues = self.service.analyze_project(snapshot)["issues"]
        self.assertEqual(["asset_lineage_invalid"], [issue["kind"] for issue in issues])
        self.assertEqual([{"asset_id": "root_asset", "status": "superseded"}], issues[0]["upstream_invalid_assets"])

    def test_linked_assets_alone_do_not_create_issue(self) -> None:
        linked_only = card("linked_only", status="accepted")
        linked_only.linked_assets = ["old_asset"]
        snapshot = self._snapshot([linked_only], [asset("old_asset", status="superseded")], [])
        self.assertEqual([], self.service.analyze_project(snapshot)["issues"])

    def test_mutation_hint_returns_recursive_downstream_without_full_issues(self) -> None:
        producer = card("producer", status="accepted", outputs=[output("a", "asset_a")])
        middle = card("middle", inputs=[{"label": "a", "asset_id": "asset_a"}], outputs=[output("b", "asset_b")])
        leaf = card("leaf", inputs=[{"label": "b", "asset_id": "asset_b"}])
        snapshot = self._snapshot(
            [producer, middle, leaf],
            [asset("asset_a", run_id="run_a", role="a"), asset("asset_b", run_id="run_b", role="b", depends_on=["asset_a"])],
            [run("run_a", "producer"), run("run_b", "middle")],
        )
        hint = self.service.mutation_hint(snapshot, "producer")
        self.assertTrue(hint["dependency_attention_check_recommended"])
        self.assertEqual(["middle", "leaf"], hint["repair_execution_order_hint"])
        self.assertNotIn("dependency_attention", hint)

    def test_inspect_source_downstream_returns_issues_and_upstream_first_order(self) -> None:
        producer = card("producer", status="accepted", outputs=[output("a", "new_a")])
        middle = card("middle", status="accepted", inputs=[{"label": "a", "asset_id": "old_a"}])
        snapshot = self._snapshot(
            [producer, middle],
            [asset("old_a", run_id="run_old", role="a"), asset("new_a", run_id="run_new", role="a")],
            [run("run_old", "producer"), run("run_new", "producer")],
        )
        result = self.service.inspect(snapshot, source_card_id="producer", include_recursive_downstream=True)
        self.assertEqual(["middle"], result["repair_execution_order"])
        self.assertEqual(1, result["issue_count"])
        self.assertEqual("input_asset_outdated", result["dependency_attention"][0]["kind"])


if __name__ == "__main__":
    unittest.main()
