from pathlib import Path
import shutil
import tempfile
import time
import unittest

from app.core.config import get_settings
from app.models.chat import ChatRequest
from app.models.patches import GraphPatch
from app.services.manifest_service import ManifestService
from app.services.manager_planner import ManagerPlanDraft
from app.services.manager_service import ManagerService
from app.services.patch_apply import PatchApplyService
from app.services.patch_validator import PatchValidator
from app.services.project_service import ProjectService
from app.services.runtime_approval_service import RuntimeApprovalService
from app.services.result_asset_service import ResultAssetService
from app.services.worker_service import WorkerService


class StubPlanner:
    def plan(self, snapshot: dict, chat_request: ChatRequest, extra_context: dict | None = None) -> ManagerPlanDraft:
        return ManagerPlanDraft.model_validate(
            {
                "response_type": "proposal",
                "message": "我建议新增免疫浸润分析模块。",
                "title": "新增免疫浸润分析",
                "summary": "我建议新增“免疫浸润分析”模块，依赖当前已接受的 DEG 结果。",
                "impact_summary": "会新增一个下游模块和卡片，不影响现有差异表达结果。",
                "patch_type": "add_module",
                "reason": chat_request.message,
                "ops": [
                    {
                        "op": "create_module",
                        "payload": {
                            "module_id": "module_immune_infiltration_test",
                            "title": "免疫浸润分析",
                            "status": "planned",
                            "summary": "基于已接受的 DEG 结果进行免疫浸润分析。",
                            "depends_on_assets": ["deg_table_v1"],
                            "expected_outputs": ["immune_score_table", "immune_heatmap"],
                            "linked_cards": ["card_immune_infiltration_test"],
                        },
                    },
                    {
                        "op": "create_card",
                        "payload": {
                            "card_id": "card_immune_infiltration_test",
                            "card_type": "module",
                            "title": "免疫浸润分析",
                            "status": "planned",
                            "summary": "基于差异表达结果新增免疫浸润分析模块。",
                            "why": "用于解释免疫相关微环境变化。",
                            "inputs": [],
                            "outputs": [],
                            "key_findings": [],
                            "manager_review": "待执行。",
                            "next_actions": ["开始执行", "修改方案", "取消模块"],
                            "linked_modules": ["module_immune_infiltration_test"],
                            "linked_runs": [],
                            "linked_assets": ["deg_table_v1"],
                        },
                    },
                ],
            }
        )


class ManagerFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="blueprint-re-test-")
        settings = get_settings()
        settings.data_root = Path(self.tmpdir)
        self.project_service = ProjectService()
        self.project_service.create_project(
            project_id="test-project",
            name="Test Project",
            current_goal="Test flow",
            seed_demo=True,
        )
        self.manager = ManagerService(self.project_service, planner=StubPlanner())
        self.validator = PatchValidator(self.project_service)
        self.apply = PatchApplyService(self.project_service, self.validator)
        self.manifest_service = ManifestService(self.project_service)
        self.runtime_approval_service = RuntimeApprovalService(self.project_service)
        self.result_asset_service = ResultAssetService(self.project_service)
        self.worker = WorkerService(self.project_service, self.manifest_service, self.runtime_approval_service)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_chat_accept_patch(self) -> None:
        response = self.manager.chat("test-project", ChatRequest(message="客户想增加免疫浸润分析模块"))
        self.assertIsNotNone(response.proposal)
        proposal = self.manager.accept_proposal("test-project", response.proposal.proposal_id)
        patch_payload = self.project_service.graph_store("test-project").load_patch(proposal.patch_id)
        result = self.apply.apply_patch("test-project", GraphPatch.model_validate(patch_payload))
        snapshot = self.project_service.get_project_snapshot("test-project")
        self.assertTrue(result.commit_hash)
        self.assertTrue(any(card.title == "免疫浸润分析" and card.status == "planned" for card in snapshot["cards"]))

    def test_run_and_review(self) -> None:
        run = self.worker.start_run("test-project", "card_enrichment_group")
        self._wait_for_run("test-project", run["run_id"])
        snapshot = self.project_service.get_project_snapshot("test-project")
        card = next(item for item in snapshot["cards"] if item.card_id == "card_enrichment_group")
        self.assertEqual(card.status, "needs_review")
        self.worker.review_run("test-project", run["run_id"], accept=True)
        snapshot = self.project_service.get_project_snapshot("test-project")
        card = next(item for item in snapshot["cards"] if item.card_id == "card_enrichment_group")
        self.assertEqual(card.status, "accepted")
        self.assertTrue(any(asset.created_by_run == run["run_id"] for asset in snapshot["graph"].assets))
        preview_asset = next(asset for asset in snapshot["graph"].assets if asset.created_by_run == run["run_id"] and asset.asset_type == "figure")
        detail = self.result_asset_service.get_asset_detail("test-project", preview_asset.asset_id)
        self.assertEqual(detail["preview"]["kind"], "image")

    def test_modify_proposal(self) -> None:
        response = self.manager.chat("test-project", ChatRequest(message="客户想增加免疫浸润分析模块"))
        proposal = self.manager.modify_proposal("test-project", response.proposal.proposal_id, ChatRequest(message="把说明改得更强调 DEG 依赖"))
        self.assertEqual(proposal.proposal_id, response.proposal.proposal_id)
        self.assertTrue(proposal.patch_id.startswith("patch_"))

    def _wait_for_run(self, project_id: str, run_id: str) -> None:
        deadline = time.time() + 5
        while time.time() < deadline:
            snapshot = self.project_service.get_project_snapshot(project_id)
            run = next(item for item in snapshot["graph"].runs if item.run_id == run_id)
            if run.status in {"success", "failed"}:
                return
            time.sleep(0.05)
        self.fail(f"run {run_id} did not finish in time")


if __name__ == "__main__":
    unittest.main()
