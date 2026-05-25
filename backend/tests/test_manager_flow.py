import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.models.cards import Card
from app.models.chat import ChatRequest, ChatSessionMessage
from app.models.executor import ExecutorContext
from app.models.graph import Asset, Claim, GraphState, Module, ModuleRef
from app.models.patches import GraphPatch, ValidationResult
from app.models.runs import CreatedAsset, ExecutorValidationReport, ExpectedOutput, Manifest, TaskPacket
from app.services.chat_session_service import ChatSessionService
from app.services.chat_job_service import ChatJobService
from app.services.executor_reviewer_worker import ExecutorReviewerWorker
from app.services.executor_validation_service import ExecutorValidationService
from app.services.flow_service import FlowService
from app.services.manager_planner import DeepSeekManagerPlanner, ManagerPlanningError
from app.services.manager_service import ManagerService
from app.services.manifest_service import ManifestService
from app.services.patch_apply import PatchApplyService
from app.services.patch_validator import PatchValidator
from app.services.project_file_service import ProjectFileService
from app.services.project_service import ProjectService
from app.services.result_asset_service import ResultAssetService
from app.services.runtime_dependency_job_service import RuntimeDependencyJobService
from app.services.runtime_approval_service import RuntimeApprovalService
from app.services.utils import atomic_write_json
from app.services.worker_service import WorkerService, _redact_command_for_log
from app.workers import agent_cli_executor
from app.workers.base import PermissionRequest, WorkerAdapter, WorkerLaunchSpec
from app.workers.shell_worker import ShellWorkerAdapter


TEST_DEFAULT_FORMATS = {
    "figure": ["svg", "png", "pdf"],
    "table": ["tsv", "csv"],
    "document": ["md", "html", "txt"],
    "model": ["rds", "h5ad"],
    "archive": ["zip", "tar.gz"],
    "binary": [],
}


def _output_role(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "output"


def output_contract(
    label: str,
    *,
    role: str | None = None,
    artifact_class: str = "table",
    accepted_formats: list[str] | None = None,
    preferred_format: str | None = None,
    asset_id: str | None = None,
    status: str | None = None,
    required: bool = True,
    description: str | None = None,
) -> dict[str, Any]:
    formats = list(accepted_formats) if accepted_formats is not None else list(TEST_DEFAULT_FORMATS.get(artifact_class, []))
    return {
        "role": role or _output_role(label),
        "label": label,
        "artifact_class": artifact_class,
        "accepted_formats": formats,
        "preferred_format": preferred_format or (formats[0] if formats else None),
        "asset_id": asset_id,
        "status": status,
        "required": required,
        "description": description,
    }


def expected_output_spec(
    role: str,
    path_hint: str,
    *,
    artifact_class: str = "table",
    accepted_formats: list[str] | None = None,
    preferred_format: str | None = None,
    label: str | None = None,
    asset_id: str | None = None,
) -> dict[str, Any]:
    formats = list(accepted_formats) if accepted_formats is not None else list(TEST_DEFAULT_FORMATS.get(artifact_class, []))
    return {
        "role": role,
        "label": label or role,
        "artifact_class": artifact_class,
        "accepted_formats": formats,
        "preferred_format": preferred_format or (formats[0] if formats else None),
        "path_hint": path_hint,
        "asset_id": asset_id,
    }


class AnswerOnlyPlanner:
    def agent_turn(self, messages: list[dict], tools: list[dict]) -> dict:
        return {"content": [{"type": "text", "text": "这是 DeepSeek 普通聊天回复。"}]}


class FailingPlanner:
    def agent_turn(self, messages: list[dict], tools: list[dict]) -> dict:
        raise ManagerPlanningError("agent llm failed")


class StubGitService:
    def commit(self, _message: str) -> str:
        return "test-commit"

    def log(self, limit: int = 20) -> list[dict[str, str]]:
        return []

    def head(self) -> str:
        return "test-commit"


class NeedsApprovalWorkerAdapter(WorkerAdapter):
    name = "needs_approval_stub"

    def build_launch_spec(self, *, packet, packet_path, run_dir, project_root, settings) -> WorkerLaunchSpec:
        return WorkerLaunchSpec(
            command=[],
            cwd=project_root,
            environment={},
            permission_requests=[
                PermissionRequest(
                    request_id=f"perm_{packet.task_id}_network",
                    target="api.example.test",
                    action="network",
                    reason="Need user approval for network access.",
                )
            ],
        )


class RaisingValidationService:
    def validate_run(self, _project_id: str, _run_id: str) -> object:
        raise RuntimeError("validator exploded")


class RaisingReviewerWorker:
    def review(self, **_kwargs) -> dict:
        raise RuntimeError("reviewer api unavailable")


class InlineCommandWorkerAdapter(WorkerAdapter):
    def __init__(self, name: str, script: str) -> None:
        self.name = name
        self.script = script

    def build_launch_spec(self, *, packet, packet_path, run_dir, project_root, settings) -> WorkerLaunchSpec:
        return WorkerLaunchSpec(
            command=[sys.executable, "-c", self.script, str(packet_path), str(run_dir), str(project_root)],
            cwd=project_root,
            environment={"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
            permission_requests=[],
        )


class StartupInitializationTest(unittest.TestCase):
    def test_main_startup_initializes_worker_service_for_reconcile(self) -> None:
        import app.main as main

        with patch("app.main.get_worker_service") as get_worker_service:
            main.initialize_runtime_services()

        get_worker_service.assert_called_once_with()


class ManagerFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="blueprint-re-test-")
        settings = get_settings()
        self._original_deepseek_api_key = settings.deepseek_api_key
        self._original_pi_command = settings.pi_command
        self._original_default_worker_type = settings.default_worker_type
        self._original_executor_sandbox_mode = settings.executor_sandbox_mode
        self._original_executor_extra_ro_binds = settings.executor_extra_ro_binds
        self._original_executor_conda_base = settings.executor_conda_base
        self._original_worker_timeout_seconds = settings.worker_timeout_seconds
        settings.deepseek_api_key = None
        settings.default_worker_type = "pi"
        settings.executor_sandbox_mode = "none"
        settings.executor_extra_ro_binds = ""
        settings.pi_command = (
            "{python} -m app.workers.demo_executor --task-packet {task_packet_path} --run-dir {run_dir} --project-root {project_root}"
        )
        settings.data_root = Path(self.tmpdir)
        self.project_service = ProjectService()
        self.project_service.create_project(
            project_id="test-project",
            name="Test Project",
            current_goal="Test flow",
            seed_demo=True,
        )
        self.project_service.git_service = lambda _project_id: StubGitService()
        self.runtime_dependency_job_service = RuntimeDependencyJobService()
        self.manager = ManagerService(
            self.project_service,
            planner=AnswerOnlyPlanner(),
            runtime_dependency_job_service=self.runtime_dependency_job_service,
        )
        self.validator = PatchValidator(self.project_service)
        self.apply = PatchApplyService(self.project_service, self.validator)
        self.manifest_service = ManifestService(self.project_service)
        self.runtime_approval_service = RuntimeApprovalService(self.project_service)
        self.result_asset_service = ResultAssetService(self.project_service)
        self.project_file_service = ProjectFileService(self.project_service)
        self.chat_session_service = ChatSessionService(self.project_service)
        self.worker = WorkerService(self.project_service, self.manifest_service, self.runtime_approval_service)
        self.flow_service = FlowService(self.project_service)

    def tearDown(self) -> None:
        settings = get_settings()
        settings.deepseek_api_key = self._original_deepseek_api_key
        settings.pi_command = self._original_pi_command
        settings.default_worker_type = self._original_default_worker_type
        settings.executor_sandbox_mode = self._original_executor_sandbox_mode
        settings.executor_extra_ro_binds = self._original_executor_extra_ro_binds
        settings.executor_conda_base = self._original_executor_conda_base
        settings.worker_timeout_seconds = self._original_worker_timeout_seconds
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _add_single_submodule_group_fixture(self) -> tuple[str, str, str, str]:
        store = self.project_service.graph_store("test-project")
        graph = store.load_graph()
        cards = store.load_cards()
        group_module_id = "module_runtime_group"
        child_module_id = "module_runtime_child"
        group_card_id = "card_runtime_group"
        child_card_id = "card_runtime_child"
        graph.modules.append(
            Module(
                module_id=child_module_id,
                title="Runtime Child Module",
                type="analysis_module",
                status="planned",
                summary="Child module under runtime group.",
                depends_on_assets=["deg_table_v1"],
                expected_outputs=["runtime_child_output"],
                linked_cards=[child_card_id],
                linked_runs=[],
                created_by="test",
                created_at="2026-05-20T00:00:00Z",
            )
        )
        graph.modules.append(
            Module(
                module_id=group_module_id,
                title="Runtime Group Module",
                type="module_group",
                status="planned",
                summary="Parent group module for runtime aggregation tests.",
                depends_on_assets=["deg_table_v1"],
                expected_outputs=["runtime_child_output"],
                linked_cards=[group_card_id],
                linked_runs=[],
                submodules=[ModuleRef(module_id=child_module_id, title="Runtime Child Module", status="planned")],
                created_by="test",
                created_at="2026-05-20T00:00:00Z",
            )
        )
        cards.append(
            Card.model_validate(
                {
                    "card_id": group_card_id,
                    "card_type": "module_group",
                    "title": "Runtime Group Card",
                    "status": "planned",
                    "aggregate_status": "partially_planned",
                    "summary": "Parent group card for runtime aggregation tests.",
                    "why": "Exercise group aggregation during executor lifecycle.",
                    "inputs": [{"label": "DEG table", "asset_id": "deg_table_v1"}],
                    "outputs": [output_contract("Runtime child output", status="planned")],
                    "linked_modules": [group_module_id],
                }
            )
        )
        cards.append(
            Card.model_validate(
                {
                    "card_id": child_card_id,
                    "card_type": "module",
                    "title": "Runtime Child Card",
                    "status": "planned",
                    "summary": "Child card whose run state should bubble to the parent group.",
                    "why": "Exercise module-group synchronization.",
                    "inputs": [{"label": "DEG table", "asset_id": "deg_table_v1"}],
                    "outputs": [output_contract("Runtime child output")],
                    "linked_modules": [child_module_id],
                    "next_actions": ["开始执行"],
                }
            )
        )
        store.save_graph(graph)
        store.save_cards(cards)
        return group_module_id, child_module_id, group_card_id, child_card_id

    def test_run_and_review(self) -> None:
        run = self.worker.start_run("test-project", "card_enrichment_group")
        self._wait_for_run("test-project", run["run_id"])
        snapshot = self.project_service.get_project_snapshot("test-project")
        card = next(item for item in snapshot["cards"] if item.card_id == "card_enrichment_group")
        self.assertEqual(card.status, "accepted")
        self.assertTrue(any(asset.created_by_run == run["run_id"] for asset in snapshot["graph"].assets))
        preview_asset = next(asset for asset in snapshot["graph"].assets if asset.created_by_run == run["run_id"] and asset.asset_type == "figure")
        detail = self.result_asset_service.get_asset_detail("test-project", preview_asset.asset_id)
        self.assertEqual(detail["preview"]["kind"], "image")

    def test_project_snapshot_includes_system_python_runtime(self) -> None:
        snapshot = self.project_service.get_project_snapshot("test-project")
        runtimes = snapshot["python_runtimes"]

        self.assertTrue(any(item["name"] == "__system__" and item["label"] == "System Python" for item in runtimes))

    def test_project_snapshot_includes_system_r_runtime(self) -> None:
        snapshot = self.project_service.get_project_snapshot("test-project")
        runtimes = snapshot["r_runtimes"]

        self.assertTrue(any(item["name"] == "__system__" and item["label"] == "System R" for item in runtimes))

    def test_project_runtime_preferences_persist_to_snapshot_and_metadata(self) -> None:
        prefs = self.project_service.update_project_runtime_preferences(
            "test-project",
            {
                "script_preference": "prefer_r",
                "python_runtime": "omicverse",
                "r_runtime": "bioconductor",
            },
        )
        self.assertEqual("prefer_r", prefs.script_preference)
        self.assertEqual("omicverse", prefs.python_runtime)
        self.assertEqual("bioconductor", prefs.r_runtime)

        snapshot = self.project_service.get_project_snapshot("test-project")
        self.assertEqual("prefer_r", snapshot["project"].runtime_preferences.script_preference)
        self.assertEqual("omicverse", snapshot["project"].runtime_preferences.python_runtime)
        self.assertEqual("bioconductor", snapshot["project"].runtime_preferences.r_runtime)
        self.assertEqual("omicverse", snapshot["graph"].metadata["default_conda_env"])
        self.assertEqual("bioconductor", snapshot["graph"].metadata["default_r_env"])

    def test_configure_card_execution_can_set_skills_and_mcp_servers(self) -> None:
        result = self.manager.blueprint_tools.configure_card_execution(
            "test-project",
            {
                "card_id": "card_immune_module",
                "skills": ["skill_a", "skill_b"],
                "mcp_servers": ["omicverse"],
            },
        )
        self.assertEqual(["card_immune_module"], result["updated_card_ids"])
        snapshot = self.project_service.get_project_snapshot("test-project")
        card = next(item for item in snapshot["cards"] if item.card_id == "card_immune_module")
        self.assertEqual(["skill_a", "skill_b"], card.executor_context.skills)
        self.assertEqual(["omicverse"], card.executor_context.mcp_servers)

    def test_library_registry_manager_tools_support_list_search_and_detail(self) -> None:
        skills = self.manager.blueprint_tools.list_skill_library("test-project")
        self.assertEqual("skill", skills["kind"])
        self.assertIn("items", skills)
        if skills["items"]:
            self.assertEqual({"id", "kind", "name", "enabled"}, set(skills["items"][0].keys()))
        search = self.manager.blueprint_tools.search_mcp_library(
            "test-project",
            {"query": "omics runtime", "top_k": 5},
        )
        self.assertEqual("mcp", search["kind"])
        self.assertTrue(search["items"])
        self.assertEqual({"id", "kind", "name", "enabled", "score"}, set(search["items"][0].keys()))
        detail = self.manager.blueprint_tools.get_mcp_library_item("test-project", "omicverse")
        self.assertEqual("omicverse", detail["item"]["id"])
        self.assertIn("summary_short", detail["item"])
        self.assertEqual("test-project", detail["project_id"])

    def test_manager_lightweight_project_tools_avoid_full_payloads(self) -> None:
        summary = self.manager.blueprint_tools.inspect_project_summary("test-project")
        self.assertIn("counts", summary)
        self.assertTrue(summary["cards"])
        self.assertIn("card_id", summary["cards"][0])
        self.assertNotIn("executor_context", summary["cards"][0])

        cards = self.manager.blueprint_tools.find_cards("test-project", {"query": "immune", "limit": 5})
        self.assertTrue(cards["items"])
        self.assertLessEqual(len(cards["items"]), 5)
        self.assertNotIn("executor_context", cards["items"][0])

        assets = self.manager.blueprint_tools.find_assets("test-project", {"query": "deg", "limit": 5})
        self.assertTrue(assets["items"])
        self.assertIn("asset_id", assets["items"][0])

        card_id = cards["items"][0]["card_id"]
        detail = self.manager.blueprint_tools.get_card_detail("test-project", card_id)
        self.assertEqual(card_id, detail["card"]["card_id"])
        self.assertIn("executor_context", detail["card"])

        plan = self.manager.blueprint_tools.plan_card_write(
            "test-project",
            {
                "action": "create",
                "card": {
                    "card_id": "planned_lightweight_card",
                    "card_type": "module",
                    "title": "Lightweight planned card",
                    "status": "planned",
                    "step": 2,
                    "summary": "Exercise lightweight planning.",
                    "inputs": [{"label": "DEG table", "asset_id": "deg_table_v1"}],
                    "outputs": [output_contract("Lightweight output", role="lightweight_output")],
                },
            },
        )
        self.assertTrue(plan["ok"])
        self.assertEqual("planned_lightweight_card", plan["card"]["card_id"])
        self.assertNotIn("timeline", plan)

        created = self.manager.blueprint_tools.create_card("test-project", plan["card"] | {"outputs": plan["normalized_outputs"]})
        self.assertTrue(created["ok"])
        self.assertEqual("planned_lightweight_card", created["card_id"])
        self.assertNotIn("timeline", created)

    def test_review_replaces_planned_output_asset_ids_with_materialized_assets(self) -> None:
        card = Card.model_validate(
            {
                "card_id": "card_qc_replace_outputs",
                "card_type": "module",
                "title": "QC Replace Outputs",
                "status": "needs_review",
                "summary": "Create a planned output then replace it after review.",
                "outputs": [output_contract("filtered counts", role="filtered_counts", asset_id="planned_filtered_counts")],
            }
        )
        real_asset = Asset(
            asset_id="asset_run_001_filtered_counts_1",
            asset_type="table",
            title="QC filtered counts",
            status="valid",
            path="results/qc/run_001/filtered_counts.tsv",
            summary="Filtered counts output.",
        )
        manifest_asset = CreatedAsset.model_validate(
            {
                "role": "filtered_counts",
                "path": "results/qc/run_001/filtered_counts.tsv",
            }
        )
        expected_output = ExpectedOutput.model_validate(
            expected_output_spec(
                "filtered_counts",
                "results/qc/run_001/filtered_counts.tsv",
                asset_id="planned_filtered_counts",
            )
        )

        self.worker._sync_card_outputs(
            card,
            [real_asset],
            manifest_created_assets=[manifest_asset],
            expected_outputs=[expected_output],
        )

        output_asset_ids = [item.asset_id for item in card.outputs]
        self.assertNotIn("planned_filtered_counts", output_asset_ids)
        self.assertIn("asset_run_001_filtered_counts_1", output_asset_ids)

    def test_rebind_downstream_inputs_replaces_superseded_upstream_assets(self) -> None:
        producer = Card.model_validate(
            {
                "card_id": "card_qc",
                "card_type": "module",
                "title": "QC",
                "status": "accepted",
                "summary": "QC",
                "linked_assets": ["asset_old_counts"],
                "outputs": [output_contract("counts", role="filtered_counts", asset_id="asset_old_counts")],
            }
        )
        consumer = Card.model_validate(
            {
                "card_id": "card_pca",
                "card_type": "module",
                "title": "PCA",
                "status": "planned",
                "summary": "PCA",
                "inputs": [{"label": "counts", "asset_id": "asset_old_counts"}],
                "linked_assets": ["asset_old_counts"],
            }
        )
        module = Module(
            module_id="module_pca",
            title="PCA",
            type="analysis_module",
            status="planned",
            summary="PCA module",
            depends_on_assets=["asset_old_counts"],
            created_by="test",
            created_at="2026-05-21T00:00:00Z",
        )
        old_asset = Asset(
            asset_id="asset_old_counts",
            asset_type="table",
            title="Old counts",
            status="valid",
            created_by_run="run_old",
            path="results/old/counts.tsv",
            summary="Old counts",
            metadata={"role": "filtered_counts"},
        )
        new_asset = Asset(
            asset_id="asset_new_counts",
            asset_type="table",
            title="New counts",
            status="valid",
            created_by_run="run_new",
            path="results/new/counts.tsv",
            summary="New counts",
            metadata={"role": "filtered_counts"},
        )
        downstream_asset = Asset(
            asset_id="asset_pca",
            asset_type="figure",
            title="PCA",
            status="valid",
            created_by_run="run_pca",
            path="results/pca.svg",
            depends_on=["asset_old_counts"],
            summary="PCA output",
        )
        claim = Claim(
            claim_id="claim_pca",
            text="PCA claim",
            status="valid",
            depends_on_assets=["asset_old_counts"],
        )

        rebinds = self.worker._rebind_downstream_inputs(
            cards=[producer, consumer],
            modules=[module],
            assets=[old_asset, new_asset, downstream_asset],
            claims=[claim],
            producer_card=producer,
            previous_outputs_by_role={"filtered_counts": old_asset},
            new_assets=[new_asset],
        )

        self.assertTrue(rebinds)
        self.assertEqual("asset_new_counts", consumer.inputs[0].asset_id)
        self.assertEqual(["asset_new_counts"], consumer.linked_assets)
        self.assertEqual(["asset_new_counts"], module.depends_on_assets)
        self.assertEqual(["asset_new_counts"], downstream_asset.depends_on)
        self.assertEqual("stale", downstream_asset.status)
        self.assertEqual(["asset_new_counts"], claim.depends_on_assets)
        self.assertEqual("stale", claim.status)

    def test_project_files_include_uploads_and_execution_files(self) -> None:
        project_root = self.project_service.project_path("test-project")
        upload_path = project_root / "data" / "uploads" / "upload_demo_notes.txt"
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        upload_path.write_text("demo notes\n", encoding="utf-8")

        graph_store = self.project_service.graph_store("test-project")
        graph = graph_store.load_graph()
        graph.assets.append(
            Asset(
                asset_id="upload_demo_notes",
                asset_type="text",
                title="demo_notes.txt",
                status="candidate",
                path="data/uploads/upload_demo_notes.txt",
                summary="Uploaded notes",
                metadata={"source": "manager_chat_upload"},
            )
        )
        graph_store.save_graph(graph)

        run = self.worker.start_run("test-project", "card_enrichment_group")
        self._wait_for_run("test-project", run["run_id"])

        files = self.project_file_service.list_files("test-project")
        self.assertTrue(any(asset.asset_id == "upload_demo_notes" for asset in files["session_uploads"]))
        self.assertTrue(any(asset.asset_id == "deg_table_v1" for asset in files["data_assets"]))
        self.assertTrue(any(asset.asset_id == "deg_table_v1" for asset in files["active_data_assets"]))
        self.assertTrue(any(item["category"] == "adapter_contract" for item in files["execution_files"]))
        self.assertTrue(any(item["category"] == "executor_brief" for item in files["execution_files"]))
        self.assertTrue(any(item["category"] == "executor_prompt" for item in files["execution_files"]))
        self.assertTrue(any(item["category"] == "filesystem_audit" for item in files["execution_files"]))
        self.assertTrue(any(item["category"] == "manifest" for item in files["execution_files"]))
        self.assertTrue(any(item["category"] == "transcript" for item in files["execution_files"]))
        agent_trace = self.project_service.project_path("test-project") / "runs" / run["run_id"] / "agent_trace.json"
        agent_trace.write_text('{"status":"success"}\n', encoding="utf-8")
        files = self.project_file_service.list_files("test-project")
        self.assertTrue(any(item["category"] == "agent_trace" for item in files["execution_files"]))

    def test_project_files_classify_superseded_assets_as_stale(self) -> None:
        graph_store = self.project_service.graph_store("test-project")
        graph = graph_store.load_graph()
        graph.assets.append(
            Asset(
                asset_id="old_run_asset",
                asset_type="table",
                title="Old run output",
                status="superseded",
                created_by_run="run_old",
                path="results/card_enrichment_group/run_old/old.tsv",
                summary="Old output",
            )
        )
        graph_store.save_graph(graph)

        files = self.project_file_service.list_files("test-project")

        self.assertTrue(any(asset.asset_id == "old_run_asset" for asset in files["stale_data_assets"]))
        self.assertFalse(any(asset.asset_id == "old_run_asset" for asset in files["active_data_assets"]))

    def test_project_files_delete_session_upload_only(self) -> None:
        project_root = self.project_service.project_path("test-project")
        upload_path = project_root / "data" / "uploads" / "upload_demo_notes.txt"
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        upload_path.write_text("demo notes\n", encoding="utf-8")

        graph_store = self.project_service.graph_store("test-project")
        graph = graph_store.load_graph()
        graph.assets.append(
            Asset(
                asset_id="upload_demo_notes",
                asset_type="text",
                title="demo_notes.txt",
                status="candidate",
                path="data/uploads/upload_demo_notes.txt",
                summary="Uploaded notes",
                metadata={"source": "manager_chat_upload"},
            )
        )
        graph_store.save_graph(graph)

        removed = self.project_file_service.delete_session_upload("test-project", "upload_demo_notes")
        self.assertEqual("upload_demo_notes", removed.asset_id)
        self.assertFalse(upload_path.exists())
        files = self.project_file_service.list_files("test-project")
        self.assertFalse(any(asset.asset_id == "upload_demo_notes" for asset in files["session_uploads"]))
        with self.assertRaises(PermissionError):
            self.project_file_service.delete_session_upload("test-project", "deg_table_v1")

    def test_project_files_delete_unreferenced_data_asset(self) -> None:
        project_root = self.project_service.project_path("test-project")
        asset_path = project_root / "data" / "scratch_unused.tsv"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_text("id\tvalue\nx\t1\n", encoding="utf-8")

        graph_store = self.project_service.graph_store("test-project")
        graph = graph_store.load_graph()
        graph.assets.append(
            Asset(
                asset_id="scratch_unused",
                asset_type="table",
                title="scratch_unused.tsv",
                status="candidate",
                path="data/scratch_unused.tsv",
                summary="Unused scratch asset",
            )
        )
        graph_store.save_graph(graph)

        removed = self.project_file_service.delete_data_asset("test-project", "scratch_unused")

        self.assertEqual("scratch_unused", removed.asset_id)
        self.assertFalse(asset_path.exists())
        graph = graph_store.load_graph()
        self.assertFalse(any(asset.asset_id == "scratch_unused" for asset in graph.assets))

    def test_project_files_refuse_to_delete_card_referenced_data_asset(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.project_file_service.delete_data_asset("test-project", "deg_table_v1")

        self.assertIn("still referenced by cards", str(ctx.exception))

    def test_rerun_card_cleans_previous_execution_files(self) -> None:
        run = self.worker.start_run("test-project", "card_enrichment_group")
        self._wait_for_run("test-project", run["run_id"])
        project_root = self.project_service.project_path("test-project")
        self.assertTrue((project_root / "runs" / run["run_id"]).exists())
        self.assertTrue((project_root / "scripts" / "generated" / run["run_id"]).exists())

        rerun = self.worker.rerun_card("test-project", "card_enrichment_group")
        self._wait_for_run("test-project", rerun["run_id"])

        self.assertFalse((project_root / "runs" / run["run_id"]).exists())
        self.assertFalse((project_root / "scripts" / "generated" / run["run_id"]).exists())
        self.assertTrue((project_root / "runs" / rerun["run_id"]).exists())

    def test_chat_sessions_persist_messages_and_derive_summary(self) -> None:
        session = self.chat_session_service.create_session("test-project")
        self.assertEqual("新会话", session.summary)

        saved = self.chat_session_service.save_session(
            "test-project",
            session.session_id,
            [
                ChatSessionMessage(id="msg_user", role="user", content="帮我重新梳理一下 DEG 之后还缺哪些分析步骤", state="done"),
                ChatSessionMessage(id="msg_manager", role="manager", content="我建议补充功能富集和免疫浸润分析。", state="done"),
            ],
        )

        self.assertEqual("帮我重新梳理一下 DEG 之后还缺哪些分析步骤", saved.summary)
        fetched = self.chat_session_service.get_session("test-project", session.session_id)
        self.assertEqual(2, len(fetched.messages))

        listed = self.chat_session_service.list_sessions("test-project")
        self.assertEqual(session.session_id, listed[0].session_id)
        self.assertEqual(2, listed[0].message_count)

    def test_delete_chat_session_removes_persisted_thread(self) -> None:
        first = self.chat_session_service.create_session("test-project", "第一条")
        second = self.chat_session_service.create_session("test-project", "第二条")

        self.chat_session_service.delete_session("test-project", first.session_id)

        remaining = self.chat_session_service.list_sessions("test-project")
        self.assertEqual([second.session_id], [item.session_id for item in remaining])

    def test_plain_chat_calls_llm_answer_and_does_not_mutate_cards(self) -> None:
        before_cards = self.project_service.graph_store("test-project").load_cards()

        response = self.manager.chat("test-project", ChatRequest(message="你好，先帮我解释一下现在项目里有哪些分析模块"))

        after_cards = self.project_service.graph_store("test-project").load_cards()
        self.assertIsNone(response.proposal)
        self.assertEqual([card.model_dump() for card in before_cards], [card.model_dump() for card in after_cards])
        self.assertEqual("这是 DeepSeek 普通聊天回复。", response.message)

    def test_plain_chat_llm_error_is_not_hidden(self) -> None:
        manager = ManagerService(self.project_service, planner=FailingPlanner())
        with self.assertRaises(ManagerPlanningError):
            manager.chat("test-project", ChatRequest(message="hi"))

    def test_analysis_suggestion_question_stays_plain_chat(self) -> None:
        response = self.manager.chat("test-project", ChatRequest(message="帮我看看我现在的分析流程，有没有还可以补充的"))
        self.assertIsNone(response.proposal)
        self.assertEqual("这是 DeepSeek 普通聊天回复。", response.message)

    def test_get_project_context_exposes_cards_and_graph(self) -> None:
        context = self.manager.blueprint_tools.get_project_context("test-project")
        self.assertEqual("test-project", context["project"]["project_id"])
        self.assertTrue(any(card["card_id"] == "card_de_analysis" for card in context["cards"]))
        self.assertTrue(any(module["module_id"] == "module_de_analysis" for module in context["modules"]))
        self.assertTrue(any(asset["asset_id"] == "deg_table_v1" for asset in context["assets"]))

    def test_list_data_assets_exposes_materialized_and_planned_timeline(self) -> None:
        self.manager.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_go_bp",
                "title": "GO BP 富集分析",
                "summary": "基于 DEG 结果进行 GO BP 富集。",
                "step": 2,
                "inputs": [{"label": "DEG table", "asset_id": "deg_table_v1"}],
                "outputs": [output_contract("GO BP enrichment", role="go_bp_enrichment", asset_id="go_bp_result")],
            },
        )

        listing = self.manager.blueprint_tools.list_data_assets("test-project")
        self.assertTrue(any(asset["asset_id"] == "deg_table_v1" for asset in listing["materialized_assets"]))
        self.assertTrue(any(asset["planned"] and asset["producer_card_id"] == "card_go_bp" for asset in listing["planned_assets"]))
        enrichment = next(card for card in listing["cards"] if card["card_id"] == "card_enrichment_group")
        self.assertEqual(2, enrichment["step"])
        self.assertEqual(["deg_table_v1", "ranked_gene_list_v1"], enrichment["required_asset_ids"])
        self.assertEqual({"audit_card_tools": False}, listing["tool_policy"])

    def test_create_card_requires_explicit_step_and_assigns_output_asset_ids(self) -> None:
        result = self.manager.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_go_bp",
                "title": "GO BP 富集分析",
                "summary": "基于 DEG 结果进行 GO BP 富集。",
                "step": 2,
                "why": "解释差异基因的生物过程。",
                "inputs": [{"label": "DEG table", "asset_id": "deg_table_v1"}],
                "outputs": [output_contract("GO BP enrichment", role="go_bp_enrichment")],
                "linked_modules": ["module_group_enrichment"],
                "linked_assets": ["deg_table_v1"],
            },
        )

        card = Card.model_validate(result["card"])
        self.assertEqual("card_go_bp", card.card_id)
        self.assertEqual(2, card.step)
        self.assertTrue(card.outputs[0].asset_id)
        snapshot = self.project_service.get_project_snapshot("test-project")
        self.assertTrue(any(item.card_id == "card_go_bp" for item in snapshot["cards"]))

    def test_create_card_rejects_missing_step(self) -> None:
        with self.assertRaisesRegex(ManagerPlanningError, "Card step is required"):
            self.manager.blueprint_tools.create_card(
                "test-project",
                {
                    "card_id": "card_no_step",
                    "title": "No Step",
                    "summary": "Should fail because step is explicit metadata now.",
                    "inputs": [{"label": "DEG table", "asset_id": "deg_table_v1"}],
                    "outputs": [output_contract("result", asset_id="no_step_result")],
                },
            )

    def test_create_card_rejects_missing_input_with_retryable_error(self) -> None:
        with self.assertRaisesRegex(ManagerPlanningError, "Input asset missing_deg is missing"):
            self.manager.blueprint_tools.create_card(
                "test-project",
                {
                    "card_id": "card_missing_input",
                    "title": "Missing Input",
                    "summary": "Should fail because input is unknown.",
                    "step": 1,
                    "inputs": [{"label": "missing", "asset_id": "missing_deg"}],
                    "outputs": [output_contract("result", asset_id="missing_input_result")],
                },
            )

    def test_create_card_rejects_step_earlier_than_input_asset_timeline(self) -> None:
        self.manager.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_qc",
                "title": "QC",
                "summary": "Produce filtered counts.",
                "step": 1,
                "inputs": [{"label": "counts", "asset_id": "count_matrix_v1"}],
                "outputs": [output_contract("filtered counts", role="filtered_counts", asset_id="filtered_counts")],
            },
        )

        with self.assertRaisesRegex(ManagerPlanningError, "Increase step to at least 2"):
            self.manager.blueprint_tools.create_card(
                "test-project",
                {
                    "card_id": "card_downstream_too_early",
                    "title": "Downstream",
                    "summary": "Consumes a planned upstream output.",
                    "step": 1,
                    "inputs": [{"label": "filtered counts", "asset_id": "filtered_counts"}],
                    "outputs": [output_contract("downstream result", asset_id="downstream_result")],
                },
            )

    def test_update_card_changes_content_and_revalidates_step(self) -> None:
        self.manager.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_qc",
                "title": "QC",
                "summary": "Produce filtered counts.",
                "step": 1,
                "inputs": [{"label": "counts", "asset_id": "count_matrix_v1"}],
                "outputs": [output_contract("filtered counts", role="filtered_counts", asset_id="filtered_counts")],
            },
        )
        result = self.manager.blueprint_tools.update_card(
            "test-project",
            {
                "card_id": "card_qc",
                "title": "QC and filtering",
                "summary": "Filter low-quality samples and features.",
            },
        )
        self.assertEqual("QC and filtering", result["card"]["title"])

        with self.assertRaisesRegex(ManagerPlanningError, "Increase step to at least 2"):
            self.manager.blueprint_tools.update_card(
                "test-project",
                {
                    "card_id": "card_qc",
                    "inputs": [{"label": "own output", "asset_id": "filtered_counts"}],
                    "step": 1,
                },
            )

    def test_delete_card_marks_card_cancelled(self) -> None:
        result = self.manager.blueprint_tools.delete_card(
            "test-project",
            {"card_id": "card_immune_module", "reason": "用户暂时不做免疫浸润分析"},
        )
        self.assertEqual("cancelled", result["card"]["status"])
        self.assertIn("用户暂时不做免疫浸润分析", result["card"]["manager_review"])

        snapshot = self.project_service.get_project_snapshot("test-project")
        card = next(item for item in snapshot["cards"] if item.card_id == "card_immune_module")
        self.assertEqual("cancelled", card.status)

    def test_create_card_rejects_duplicate_card_id_and_output_asset(self) -> None:
        with self.assertRaisesRegex(ManagerPlanningError, "Duplicate card_id"):
            self.manager.blueprint_tools.create_card(
                "test-project",
                {
                    "card_id": "card_de_analysis",
                    "title": "Duplicate",
                    "summary": "Duplicate card id.",
                    "step": 1,
                    "inputs": [{"label": "counts", "asset_id": "count_matrix_v1"}],
                    "outputs": [output_contract("duplicate", asset_id="duplicate_output")],
                },
            )

        with self.assertRaisesRegex(ManagerPlanningError, "already exists as a materialized asset"):
            self.manager.blueprint_tools.create_card(
                "test-project",
                {
                    "card_id": "card_duplicate_deg",
                    "title": "Duplicate DEG",
                    "summary": "Duplicate a planned or materialized output.",
                    "step": 1,
                    "inputs": [{"label": "counts", "asset_id": "count_matrix_v1"}],
                    "outputs": [output_contract("DEG", role="deg", asset_id="deg_table_v1")],
                },
            )

        self.manager.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_first_planned_output",
                "title": "First planned output",
                "summary": "Creates a planned output asset.",
                "step": 1,
                "inputs": [{"label": "counts", "asset_id": "count_matrix_v1"}],
                "outputs": [output_contract("planned result", role="planned_result", asset_id="planned_duplicate_result")],
            },
        )
        with self.assertRaisesRegex(ManagerPlanningError, "Duplicate planned output asset_id values"):
            self.manager.blueprint_tools.create_card(
                "test-project",
                {
                    "card_id": "card_second_planned_output",
                    "title": "Second planned output",
                    "summary": "Tries to create the same planned output asset.",
                    "step": 1,
                    "inputs": [{"label": "counts", "asset_id": "count_matrix_v1"}],
                    "outputs": [output_contract("planned result", role="planned_result", asset_id="planned_duplicate_result")],
                },
            )

    def test_multi_card_timeline_and_dag_are_derived_from_card_outputs(self) -> None:
        self.project_service.create_project(
            project_id="timeline-project",
            name="Timeline Project",
            current_goal="Build a layered workflow",
            seed_demo=False,
        )
        store = self.project_service.graph_store("timeline-project")
        store.save_graph(
            GraphState(
                assets=[
                    Asset(
                        asset_id="raw_counts",
                        asset_type="count_matrix",
                        title="raw_counts.tsv",
                        status="candidate",
                        path="data/uploads/raw_counts.tsv",
                        summary="Uploaded raw count matrix.",
                    )
                ]
            )
        )
        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())

        manager.blueprint_tools.create_card(
            "timeline-project",
            {
                "card_id": "card_qc",
                "title": "数据校验与过滤",
                "summary": "过滤低质量样本并输出 filtered counts。",
                "step": 1,
                "inputs": [{"label": "Raw counts", "asset_id": "raw_counts"}],
                "outputs": [output_contract("Filtered counts", role="filtered_counts", asset_id="filtered_counts")],
            },
        )
        manager.blueprint_tools.create_card(
            "timeline-project",
            {
                "card_id": "card_deseq2",
                "title": "DESeq2 差异分析",
                "summary": "基于 filtered counts 输出 DEG 结果。",
                "step": 2,
                "inputs": [{"label": "Filtered counts", "asset_id": "filtered_counts"}],
                "outputs": [output_contract("DEG results", role="deseq2_results", asset_id="deseq2_results")],
            },
        )
        manager.blueprint_tools.create_card(
            "timeline-project",
            {
                "card_id": "card_kegg",
                "title": "KEGG 通路富集",
                "summary": "基于 DEG results 做 KEGG 富集。",
                "step": 3,
                "inputs": [{"label": "DEG results", "asset_id": "deseq2_results"}],
                "outputs": [output_contract("KEGG results", role="kegg_results", asset_id="kegg_results")],
            },
        )

        listing = manager.blueprint_tools.list_data_assets("timeline-project")
        steps = {card["card_id"]: card["step"] for card in listing["cards"]}
        self.assertEqual({"card_qc": 1, "card_deseq2": 2, "card_kegg": 3}, steps)

        flow = FlowService(self.project_service).get_asset_flow("timeline-project")
        self.assertTrue(
            any(
                edge["source_card_id"] == "card_qc"
                and edge["target_card_id"] == "card_deseq2"
                and edge["asset_id"] == "filtered_counts"
                for edge in flow["card_edges"]
            )
        )
        self.assertTrue(
            any(
                edge["source_card_id"] == "card_deseq2"
                and edge["target_card_id"] == "card_kegg"
                and edge["asset_id"] == "deseq2_results"
                for edge in flow["card_edges"]
            )
        )

        work_order = FlowService(self.project_service).get_work_order("timeline-project")
        deseq2 = next(item for item in work_order["work_items"] if item["card_id"] == "card_deseq2")
        kegg = next(item for item in work_order["work_items"] if item["card_id"] == "card_kegg")
        self.assertEqual(2, deseq2["step"])
        self.assertEqual(3, kegg["step"])
        self.assertEqual(["card_qc"], deseq2["depends_on_card_ids"])
        self.assertEqual(["card_deseq2"], kegg["depends_on_card_ids"])
        self.assertFalse(deseq2["can_start"])
        self.assertIn("upstream_cards_not_accepted", deseq2["block_reasons"])

    def test_read_result_asset_tool_returns_preview(self) -> None:
        detail = self.manager.blueprint_tools.read_result_asset("test-project", "deg_table_v1")
        self.assertEqual(detail["asset"].asset_id, "deg_table_v1")
        self.assertIn("preview", detail)

    def test_asset_flow_and_work_order_are_derived_for_ui(self) -> None:
        asset_flow = self.flow_service.get_asset_flow("test-project")
        self.assertTrue(
            any(
                edge["source_card_id"] == "card_de_analysis"
                and edge["target_card_id"] == "card_enrichment_group"
                and edge["asset_id"] == "deg_table_v1"
                for edge in asset_flow["card_edges"]
            )
        )
        self.assertTrue(
            any(
                edge["source_card_id"] is None
                and edge["target_card_id"] == "card_de_analysis"
                and edge["asset_id"] == "count_matrix_v1"
                for edge in asset_flow["card_edges"]
            )
        )

        work_order = self.flow_service.get_work_order("test-project")
        enrichment = next(item for item in work_order["work_items"] if item["card_id"] == "card_enrichment_group")
        immune = next(item for item in work_order["work_items"] if item["card_id"] == "card_immune_module")
        self.assertIn("card_de_analysis", enrichment["depends_on_card_ids"])
        self.assertTrue(enrichment["can_start"])
        self.assertFalse(immune["can_start"])
        self.assertIn("proposal_not_accepted", immune["block_reasons"])
        self.assertTrue(any("card_enrichment_group" in batch["card_ids"] for batch in work_order["parallel_batches"]))

    def test_start_run_blocks_when_work_order_cannot_start(self) -> None:
        self.project_service.create_project(
            project_id="blocked-project",
            name="Blocked Project",
            current_goal="Validate start gate",
            seed_demo=False,
        )
        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())
        store = self.project_service.graph_store("blocked-project")
        store.save_graph(
            GraphState(
                assets=[
                    Asset(
                        asset_id="raw_counts",
                        asset_type="count_matrix",
                        title="raw_counts.tsv",
                        status="candidate",
                        path="data/uploads/raw_counts.tsv",
                        summary="Uploaded raw counts.",
                    )
                ]
            )
        )
        manager.blueprint_tools.create_card(
            "blocked-project",
            {
                "card_id": "card_qc",
                "title": "QC",
                "summary": "Produce filtered counts.",
                "step": 1,
                "inputs": [{"label": "Raw counts", "asset_id": "raw_counts"}],
                "outputs": [output_contract("Filtered counts", role="filtered_counts", asset_id="filtered_counts")],
            },
        )
        manager.blueprint_tools.create_card(
            "blocked-project",
            {
                "card_id": "card_deseq2",
                "title": "DESeq2",
                "summary": "Produce DEG results.",
                "step": 2,
                "inputs": [{"label": "Filtered counts", "asset_id": "filtered_counts"}],
                "outputs": [output_contract("DEG results", role="deseq2_results", asset_id="deseq2_results")],
            },
        )

        with self.assertRaises(HTTPException) as ctx:
            self.worker.start_run("blocked-project", "card_deseq2")

        self.assertEqual(409, ctx.exception.status_code)
        self.assertIn("upstream_cards_not_accepted", ctx.exception.detail["block_details"]["block_reasons"])

    def test_task_packet_uses_card_inputs_when_linked_assets_are_empty(self) -> None:
        store = self.project_service.graph_store("test-project")
        cards = store.load_cards()
        card = next(item for item in cards if item.card_id == "card_enrichment_group")
        card.linked_assets = []
        store.save_cards(cards)

        run = self.worker.start_run("test-project", "card_enrichment_group")
        packet = self.manifest_service.load_task_packet("test-project", run["run_id"])

        self.assertEqual(["deg_table_v1"], [item.asset_id for item in packet.input_assets])
        self.assertEqual("deg_table_v1", packet.card_inputs[0].asset_id)
        self._wait_for_run("test-project", run["run_id"])

    def test_start_run_can_override_python_runtime_for_task_packet(self) -> None:
        run = self.worker.start_run("test-project", "card_enrichment_group", python_runtime="omicverse")
        packet = self.manifest_service.load_task_packet("test-project", run["run_id"])

        self.assertEqual("omicverse", packet.executor_context.runtime_bindings.conda_env)
        self.assertEqual("omicverse", packet.executor_context.runtime_bindings.env["BLUEPRINT_PYTHON_RUNTIME"])
        self._wait_for_run("test-project", run["run_id"])

    def test_start_run_can_override_r_runtime_for_task_packet(self) -> None:
        run = self.worker.start_run("test-project", "card_enrichment_group", r_runtime="bioconductor")
        packet = self.manifest_service.load_task_packet("test-project", run["run_id"])

        self.assertEqual("bioconductor", packet.executor_context.runtime_bindings.r_env)
        self.assertEqual("bioconductor", packet.executor_context.runtime_bindings.env["BLUEPRINT_R_RUNTIME"])
        self._wait_for_run("test-project", run["run_id"])

    def test_start_run_system_python_runtime_does_not_set_conda_env(self) -> None:
        run = self.worker.start_run("test-project", "card_enrichment_group", python_runtime="__system__", r_runtime="__system__")
        packet = self.manifest_service.load_task_packet("test-project", run["run_id"])

        self.assertIsNone(packet.executor_context.runtime_bindings.conda_env)
        self.assertIsNone(packet.executor_context.runtime_bindings.r_env)
        self.assertNotIn("BLUEPRINT_PYTHON_RUNTIME", packet.executor_context.runtime_bindings.env)
        self.assertNotIn("BLUEPRINT_R_RUNTIME", packet.executor_context.runtime_bindings.env)
        self._wait_for_run("test-project", run["run_id"])

    def test_manager_context_surfaces_script_preference_guidance(self) -> None:
        snapshot = self.project_service.get_project_snapshot("test-project")
        planner = DeepSeekManagerPlanner()
        context = planner._build_context(
            snapshot,
            ChatRequest(
                message="增加一个 PCA 分析卡",
                context={"script_preference": "prefer_r", "python_runtime": "omicverse", "r_runtime": "bioconductor"},
            ),
        )

        self.assertEqual("prefer_r", context["selected_context"]["script_preference"])
        self.assertEqual("omicverse", context["selected_context"]["python_runtime"])
        self.assertEqual("bioconductor", context["selected_context"]["r_runtime"])
        self.assertFalse(context["script_preference_guidance"]["hard_constraint"])
        self.assertIn("prefer R scripts", context["script_preference_guidance"]["card_instruction_block"])
        self.assertIn("omicverse", context["runtime_preference_guidance"]["card_instruction_block"])
        self.assertIn("bioconductor", context["runtime_preference_guidance"]["card_instruction_block"])
        self.assertIn("executor_context", context["op_contracts"]["create_card"]["optional_fields"])
        self.assertIn("executor_context", context["op_contracts"]["update_card"]["optional_fields"])

    def test_create_card_persists_soft_script_preference_instruction(self) -> None:
        result = self.manager.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_soft_script_preference",
                "title": "Soft script preference",
                "summary": "Exercise executor context persistence.",
                "step": 1,
                "executor_context": {
                    "instruction_blocks": [
                        "Soft script preference: prefer R scripts when practical; use Python if it is more reliable."
                    ]
                },
            },
        )

        instruction_blocks = result["card"]["executor_context"]["instruction_blocks"]
        self.assertEqual(1, len(instruction_blocks))
        self.assertIn("prefer R scripts", instruction_blocks[0])

    def test_instruction_only_executor_context_keeps_agent_worker_permissions(self) -> None:
        store = self.project_service.graph_store("test-project")
        cards = store.load_cards()
        card = next(item for item in cards if item.card_id == "card_enrichment_group")
        card.executor_context = ExecutorContext.model_validate(
            {
                "instruction_blocks": [
                    "Soft script preference: prefer R scripts when practical; use Python if it is more reliable."
                ]
            }
        )
        store.save_cards(cards)

        packet = self.worker._task_packet("test-project", "run_instruction_context", card, store.load_graph().assets, "pi")

        self.assertEqual("allow", packet.executor_context.tool_policy.network)
        self.assertTrue(packet.executor_context.tool_policy.rscript)
        self.assertIn("prefer R scripts", "\n".join(packet.executor_context.instruction_blocks))

    def test_executor_context_can_disable_default_rscript_permission(self) -> None:
        store = self.project_service.graph_store("test-project")
        cards = store.load_cards()
        card = next(item for item in cards if item.card_id == "card_enrichment_group")
        card.executor_context = ExecutorContext.model_validate({"tool_policy": {"rscript": False}})
        store.save_cards(cards)

        packet = self.worker._task_packet("test-project", "run_disable_rscript", card, store.load_graph().assets, "pi")

        self.assertEqual("allow", packet.executor_context.tool_policy.network)
        self.assertFalse(packet.executor_context.tool_policy.rscript)

    def test_manager_can_configure_card_execution_policy(self) -> None:
        result = self.manager.blueprint_tools.configure_card_execution(
            "test-project",
            {
                "card_ids": ["card_enrichment_group"],
                "tool_policy": {"network": "allow", "rscript": True},
                "runtime_bindings": {"r_env": "bioconductor"},
                "instruction_blocks": ["Use Rscript for enrichment if Python packages are insufficient."],
            },
        )

        self.assertEqual(["card_enrichment_group"], result["updated_card_ids"])
        card = next(item for item in self.project_service.graph_store("test-project").load_cards() if item.card_id == "card_enrichment_group")
        self.assertEqual("allow", card.executor_context.tool_policy.network)
        self.assertTrue(card.executor_context.tool_policy.rscript)
        self.assertEqual("bioconductor", card.executor_context.runtime_bindings.r_env)

    def test_manager_can_start_background_python_dependency_install(self) -> None:
        settings = get_settings()
        conda_base = Path(self.tmpdir) / "conda"
        python_bin = conda_base / "envs" / "rnaseq" / "bin" / "python"
        python_bin.parent.mkdir(parents=True)
        python_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        settings.executor_conda_base = conda_base

        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="installed scanpy\n",
            stderr="",
        )
        with patch("app.services.manager_blueprint_tools.subprocess.run", return_value=completed) as run:
            result = self.manager.blueprint_tools.install_runtime_dependencies(
                "test-project",
                {
                    "ecosystem": "python",
                    "runtime": "rnaseq",
                    "packages": ["scanpy"],
                    "manager": "pip",
                },
            )
            for _ in range(40):
                status = self.manager.blueprint_tools.get_runtime_dependency_install_status("test-project", result["job_id"])
                if status["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.01)

        self.assertTrue(result["ok"])
        self.assertTrue(result["background"])
        self.assertIn(result["status"], {"queued", "running", "succeeded"})
        self.assertEqual(["scanpy"], result["packages"])
        self.assertEqual("succeeded", status["status"])
        self.assertTrue(status["ok"])
        command = run.call_args.args[0]
        self.assertEqual(str(python_bin), command[0])
        self.assertEqual(["-m", "pip", "install", "scanpy"], command[1:])

    def test_manager_can_start_background_r_dependency_install(self) -> None:
        settings = get_settings()
        conda_base = Path(self.tmpdir) / "conda"
        rscript = conda_base / "envs" / "R_env" / "bin" / "Rscript"
        rscript.parent.mkdir(parents=True)
        rscript.write_text("#!/bin/sh\n", encoding="utf-8")
        settings.executor_conda_base = conda_base

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
        with patch("app.services.manager_blueprint_tools.subprocess.run", return_value=completed) as run:
            result = self.manager.blueprint_tools.install_runtime_dependencies(
                "test-project",
                {
                    "ecosystem": "r",
                    "runtime": "R_env",
                    "packages": ["GSVA", "limma"],
                    "manager": "bioconductor",
                },
            )
            for _ in range(40):
                status = self.manager.blueprint_tools.get_runtime_dependency_install_status("test-project", result["job_id"])
                if status["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.01)

        self.assertTrue(result["ok"])
        self.assertTrue(result["background"])
        self.assertEqual("succeeded", status["status"])
        self.assertTrue(status["ok"])
        command = run.call_args.args[0]
        self.assertEqual(str(rscript), command[0])
        self.assertIn("BiocManager::install", command[-1])
        self.assertIn('"GSVA"', command[-1])

    def test_manager_dependency_install_rejects_system_runtime(self) -> None:
        with self.assertRaises(ManagerPlanningError):
            self.manager.blueprint_tools.install_runtime_dependencies(
                "test-project",
                {"ecosystem": "python", "runtime": "__system__", "packages": ["scanpy"]},
            )

    def test_manager_can_write_and_filter_project_memory(self) -> None:
        preference = self.manager.blueprint_tools.write_project_memory(
            "test-project",
            {
                "memory_id": "pref_plotting_style",
                "kind": "user_preference",
                "summary": "用户明确要求记住：科研绘图偏论文发表风格，清晰克制。",
            },
        )
        correction = self.manager.blueprint_tools.write_project_memory(
            "test-project",
            {
                "memory_id": "corr_blueprint_facts",
                "kind": "correction_memory",
                "summary": "项目执行事实应从蓝图、cards、assets、runs 推断，不写入 project memory。",
            },
        )

        self.assertEqual("pref_plotting_style", preference["memory"]["memory_id"])
        self.assertEqual("corr_blueprint_facts", correction["memory"]["memory_id"])

        listing = self.manager.blueprint_tools.list_project_memory(
            "test-project",
            {"kind": "user_preference", "limit": 5},
        )

        self.assertEqual(["pref_plotting_style"], [item["memory_id"] for item in listing["items"]])
        self.assertIn("user_preference", listing["summary"])
        self.assertEqual("Use blueprint/cards/assets/runs for project execution facts.", listing["memory_policy"]["fact_source"])

    def test_project_memory_rejects_invalid_kind(self) -> None:
        with self.assertRaises(ManagerPlanningError):
            self.manager.blueprint_tools.write_project_memory(
                "test-project",
                {"kind": "project_fact", "summary": "Do not store this."},
            )

    def test_project_memory_truncates_long_summary(self) -> None:
        result = self.manager.blueprint_tools.write_project_memory(
            "test-project",
            {
                "kind": "correction_memory",
                "summary": "x" * 700,
            },
        )

        self.assertLessEqual(len(result["memory"]["summary"]), 500)
        self.assertTrue(result["memory"]["summary"].endswith("..."))

    def test_template_instantiation_without_script_bindings_blocks_start(self) -> None:
        self.manager.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_template_source",
                "card_type": "module",
                "title": "Template Source",
                "status": "accepted",
                "step": 2,
                "summary": "Stable reusable analysis card.",
                "why": "Used to seed the template library.",
                "inputs": [],
                "outputs": [output_contract("Template output")],
                "executor_context": {
                    "instruction_blocks": ["Prefer reusable scripts."],
                    "script_asset_requirements": [
                        {
                            "requirement_id": "main_analysis_script",
                            "label": "Main analysis script",
                            "description": "User-selected project script asset",
                        }
                    ],
                },
            },
        )
        saved = self.manager.blueprint_tools.save_card_template(
            "test-project",
            {
                "card_id": "card_template_source",
                "title": "Reusable Template",
                "tags": ["reuse", "scripts"],
            },
        )

        result = self.manager.blueprint_tools.instantiate_card_template(
            "test-project",
            {
                "template_id": saved["template"]["template_id"],
                "card_id": "card_template_copy",
                "title": "Template Copy",
                "step": 3,
            },
        )

        self.assertEqual("card_template_copy", result["card"]["card_id"])
        self.assertIn("Missing script asset bindings", result["card"]["progress_note"] or "")
        work_item = next(
            item for item in self.flow_service.get_work_order("test-project")["work_items"] if item["card_id"] == "card_template_copy"
        )
        self.assertFalse(work_item["can_start"])
        self.assertIn("missing_script_asset_bindings", work_item["block_reasons"])

    def test_manager_run_control_wrapper_can_start_card(self) -> None:
        manager_with_worker = ManagerService(self.project_service, planner=AnswerOnlyPlanner(), worker_service=self.worker)
        manager_with_worker.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_run_control_start",
                "card_type": "module",
                "title": "Run Control Card",
                "status": "planned",
                "step": 2,
                "summary": "Simple card for manager run-control smoke.",
                "why": "Verify manager start_card_run wrapper.",
                "inputs": [],
                "outputs": [output_contract("Result table", role="result_table")],
            },
        )

        result = manager_with_worker.blueprint_tools.start_card_run(
            "test-project",
            {
                "card_id": "card_run_control_start",
                "worker_type": "pi",
            },
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["can_start"])
        self.assertIn("run_id", result)
        self._wait_for_run("test-project", result["run_id"])

    def test_manager_can_cleanup_rejected_run_history(self) -> None:
        manager_with_worker = ManagerService(self.project_service, planner=AnswerOnlyPlanner(), worker_service=self.worker)
        run = self.worker.start_run("test-project", "card_enrichment_group")
        self._wait_for_run("test-project", run["run_id"])
        self.worker.review_run("test-project", run["run_id"], accept=False)

        result = manager_with_worker.blueprint_tools.cleanup_run_history(
            "test-project",
            {
                "run_id": run["run_id"],
                "reason": "Manager cleared rejected run history.",
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(1, result["cleaned_count"])
        self.assertEqual(0, result["skipped_count"])
        project_root = self.project_service.project_path("test-project")
        self.assertFalse((project_root / "runs" / run["run_id"]).exists())
        self.assertFalse((project_root / "results" / "card_enrichment_group" / run["run_id"]).exists())

    def test_manager_cleanup_run_history_preserves_valid_asset_runs_by_default(self) -> None:
        manager_with_worker = ManagerService(self.project_service, planner=AnswerOnlyPlanner(), worker_service=self.worker)
        run = self.worker.start_run("test-project", "card_enrichment_group")
        self._wait_for_run("test-project", run["run_id"])
        self.worker.review_run("test-project", run["run_id"], accept=True)

        result = manager_with_worker.blueprint_tools.cleanup_run_history(
            "test-project",
            {
                "run_id": run["run_id"],
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(0, result["cleaned_count"])
        self.assertEqual(1, result["skipped_count"])
        self.assertEqual("valid_assets_protected", result["skipped"][0]["reason"])
        self.assertTrue((self.project_service.project_path("test-project") / "runs" / run["run_id"]).exists())

    def test_start_run_returns_404_for_missing_card(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self.worker.start_run("test-project", "missing-card")

        self.assertEqual(404, ctx.exception.status_code)

    def test_start_run_prefers_configured_opencode(self) -> None:
        settings = self.project_service.settings
        original_command = settings.opencode_command
        settings.opencode_command = (
            "{python} -m app.workers.demo_executor --task-packet {task_packet_path} --run-dir {run_dir} --project-root {project_root}"
        )
        try:
            run = self.worker.start_run("test-project", "card_enrichment_group", worker_type="opencode")
            self.assertEqual("opencode", run["worker_type"])
            self._wait_for_run("test-project", run["run_id"])
        finally:
            settings.opencode_command = original_command

    def test_pi_and_claude_code_workers_use_wrapper_contract(self) -> None:
        settings = self.project_service.settings
        original_pi = settings.pi_command
        original_claude = settings.claude_code_command
        wrapped_demo = (
            "{python} -m app.workers.demo_executor --task-packet {task_packet_path} --run-dir {run_dir} --project-root {project_root}"
        )
        settings.pi_command = wrapped_demo
        settings.claude_code_command = wrapped_demo
        try:
            for index, worker_type in enumerate(("pi", "claude_code")):
                if index:
                    run = self.worker.rerun_card("test-project", "card_enrichment_group", worker_type=worker_type)
                else:
                    run = self.worker.start_run("test-project", "card_enrichment_group", worker_type=worker_type)
                self.assertEqual(worker_type, run["worker_type"])
                self._wait_for_run("test-project", run["run_id"])
                run_dir = self.project_service.project_path("test-project") / "runs" / run["run_id"]
                contract = json.loads((run_dir / "adapter_contract.json").read_text(encoding="utf-8"))
                self.assertEqual(worker_type, contract["worker_type"])
                events = self.project_service.graph_store("test-project").load_run_events(run["run_id"])
                self.assertTrue(any(event.event_type == "executor_progress" for event in events))
        finally:
            settings.pi_command = original_pi
            settings.claude_code_command = original_claude

    def test_task_packet_includes_executor_context_and_reporting_contract(self) -> None:
        store = self.project_service.graph_store("test-project")
        cards = store.load_cards()
        card = next(item for item in cards if item.card_id == "card_enrichment_group")
        card.executor_context = ExecutorContext.model_validate(
            {
                "executor_profile": "bioinfo_r_worker",
                "skills": ["deseq2", "gsea"],
                "instruction_blocks": ["Use curated R scripts when possible."],
                "references": [{"type": "file", "path": "configs/params.yaml"}],
                "tool_policy": {"network": "allow", "python": True, "rscript": True, "shell": True, "git_write": False},
                "runtime_bindings": {"conda_env": "rnaseq", "working_dir": "."},
            }
        )
        store.save_cards(cards)

        run = self.worker.start_run("test-project", "card_enrichment_group")
        packet = self.manifest_service.load_task_packet("test-project", run["run_id"])
        self.assertEqual("bioinfo_r_worker", packet.executor_context.executor_profile)
        self.assertEqual(["deseq2", "gsea"], packet.executor_context.skills)
        self.assertEqual("stdout_bp_event", packet.manager_reporting_contract.transport)
        self._wait_for_run("test-project", run["run_id"])

    def test_command_adapter_writes_contract_files_and_env(self) -> None:
        project_root = self.project_service.project_path("test-project")
        run_id = "run_contract_check"
        run_dir = project_root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        store = self.project_service.graph_store("test-project")
        cards = store.load_cards()
        card = next(item for item in cards if item.card_id == "card_enrichment_group")
        card.executor_context = ExecutorContext.model_validate(
            {
                "skills": ["spatial-10x-converter"],
                "mcp_servers": ["omicverse"],
                "runtime_bindings": {"conda_env": "omicverse"},
            }
        )
        store.save_cards(cards)
        packet = self.worker._task_packet("test-project", run_id, card, store.load_graph().assets, "shell")
        packet_path = run_dir / "task_packet.json"
        packet_path.write_text(json.dumps(packet.model_dump(), ensure_ascii=True), encoding="utf-8")

        adapter = ShellWorkerAdapter()
        spec = adapter.build_launch_spec(
            packet=packet,
            packet_path=packet_path,
            run_dir=run_dir,
            project_root=project_root,
            settings=self.project_service.settings,
        )

        self.assertTrue((run_dir / "executor_brief.md").exists())
        self.assertTrue((run_dir / "executor_prompt.md").exists())
        self.assertTrue((run_dir / "adapter_contract.json").exists())
        self.assertTrue((run_dir / "library" / "skill_bindings.json").exists())
        self.assertTrue((run_dir / "library" / "mcp_bindings.json").exists())
        self.assertTrue((run_dir / "library" / "mcp.json").exists())
        self.assertIn("BLUEPRINT_EXECUTOR_BRIEF", spec.environment)
        self.assertIn("BLUEPRINT_EXECUTOR_PROMPT", spec.environment)
        self.assertIn("BLUEPRINT_ADAPTER_CONTRACT", spec.environment)
        self.assertIn("BLUEPRINT_DEPENDENCY_REPORT_TOOL", spec.environment)
        self.assertIn("BLUEPRINT_PI_SKILL_PATHS", spec.environment)
        self.assertIn("BLUEPRINT_ALLOWED_PATHS", spec.environment)
        self.assertTrue(any(request.target == "scripts/generated/" for request in spec.permission_requests))
        self.assertTrue((run_dir / "report_dependency_issue.py").exists())

        contract = json.loads((run_dir / "adapter_contract.json").read_text(encoding="utf-8"))
        self.assertEqual("shell", contract["worker_type"])
        self.assertEqual("BP_EVENT ", contract["stdout_prefix"])
        self.assertEqual("executor_prompt.md", contract["executor_prompt_path"])
        self.assertEqual("manifest.candidate.json", contract["manifest_candidate_path"])
        self.assertEqual(["success", "failed", "partial"], contract["manifest_status_values"])
        self.assertEqual("dependency_issue.json", contract["dependency_issue_path"])
        self.assertEqual("report_dependency_issue.py", contract["dependency_report_tool_path"])
        self.assertEqual("report_dependency_issue", contract["executor_tools"][0]["name"])
        prompt = (run_dir / "executor_prompt.md").read_text(encoding="utf-8")
        self.assertIn("Runtime dependency policy", prompt)
        self.assertIn("report_dependency_issue.py", prompt)
        self.assertIn("validation_evidence.input_conclusion", prompt)
        self.assertIn("Do not restate the full input list in the manifest", prompt)

    def test_dependency_report_tool_marks_run_failed_with_attention(self) -> None:
        adapter = ShellWorkerAdapter()
        adapter.command_template = (
            "{python} {run_dir}/report_dependency_issue.py "
            "--ecosystem R --package clusterProfiler --package enrichplot --manager Bioconductor"
        )
        self.worker.registry["shell"] = adapter

        run = self.worker.start_run("test-project", "card_enrichment_group", worker_type="shell")
        self._wait_for_run("test-project", run["run_id"])

        snapshot = self.project_service.get_project_snapshot("test-project")
        run_record = next(item for item in snapshot["graph"].runs if item.run_id == run["run_id"])
        card = next(item for item in snapshot["cards"] if item.card_id == "card_enrichment_group")
        self.assertEqual("failed", run_record.status)
        self.assertTrue(run_record.needs_manager_attention)
        self.assertIn("clusterProfiler", run_record.summary)
        self.assertIn("clusterProfiler", card.progress_note)
        events = self.project_service.graph_store("test-project").load_run_events(run["run_id"])
        self.assertTrue(any(event.event_type == "runtime_dependency_missing" for event in events))
        run_dir = self.project_service.project_path("test-project") / "runs" / run["run_id"]
        issue = json.loads((run_dir / "dependency_issue.json").read_text(encoding="utf-8"))
        self.assertTrue(issue["blocking"])
        self.assertEqual("runtime_dependency_missing", issue["issues"][0]["metadata"]["issue_kind"])
        brief = json.loads((run_dir / "manager_brief.json").read_text(encoding="utf-8"))
        self.assertEqual("runtime_dependency_missing", brief["dependency_issues"][0]["metadata"]["issue_kind"])

    def test_command_adapter_wraps_with_bwrap_when_available(self) -> None:
        project_root = self.project_service.project_path("test-project")
        run_id = "run_bwrap_check"
        run_dir = project_root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        store = self.project_service.graph_store("test-project")
        card = next(item for item in store.load_cards() if item.card_id == "card_enrichment_group")
        packet = self.worker._task_packet("test-project", run_id, card, store.load_graph().assets, "shell")
        packet.executor_context.runtime_bindings.env["CUSTOM_RUNTIME_ENV"] = "custom-value"
        packet_path = run_dir / "task_packet.json"
        packet_path.write_text(json.dumps(packet.model_dump(), ensure_ascii=True), encoding="utf-8")

        adapter = ShellWorkerAdapter()
        self.project_service.settings.executor_sandbox_mode = "bwrap"
        self.project_service.settings.executor_extra_ro_binds = str(run_dir / "extra-ro")
        (run_dir / "extra-ro").mkdir()
        with patch("app.workers.command_worker._ensure_bwrap_runtime", return_value="/usr/bin/bwrap"):
            spec = adapter.build_launch_spec(
                packet=packet,
                packet_path=packet_path,
                run_dir=run_dir,
                project_root=project_root,
                settings=self.project_service.settings,
            )

        self.assertTrue(spec.sandboxed)
        self.assertEqual("/usr/bin/bwrap", spec.command[0])
        self.assertIn("--ro-bind", spec.command)
        self.assertIn("/", spec.command)
        self.assertIn("--tmpfs", spec.command)
        self.assertIn(str(project_root / "graph"), spec.command)
        self.assertIn("--bind", spec.command)
        self.assertIn(str(run_dir), spec.command)
        self.assertIn(str(project_root / packet.run_context.result_dir), spec.command)
        self.assertIn("CUSTOM_RUNTIME_ENV", spec.command)
        self.assertIn("custom-value", spec.command)
        self.assertIn("--clearenv", spec.command)
        self.assertIn("HOME", spec.command)
        self.assertIn(str(run_dir / "home"), spec.command)
        self.assertIn("PI_CODING_AGENT_DIR", spec.command)
        self.assertIn(str(run_dir / "state" / "pi-agent"), spec.command)
        self.assertIn("PI_CODING_AGENT_SESSION_DIR", spec.command)
        self.assertIn(str(run_dir / "state" / "pi-sessions"), spec.command)
        self.assertIn("XDG_CACHE_HOME", spec.command)
        self.assertIn(str(run_dir / "cache"), spec.command)
        self.assertNotIn("--unshare-net", spec.command)
        self.assertEqual(str(run_dir / "sandbox_plan.json"), spec.environment["BLUEPRINT_SANDBOX_PLAN"])
        sandbox_plan = json.loads((run_dir / "sandbox_plan.json").read_text(encoding="utf-8"))
        self.assertEqual("bwrap", sandbox_plan["mode"])
        self.assertEqual("host", sandbox_plan["network"])
        self.assertFalse(sandbox_plan["network_isolation"])
        self.assertTrue(sandbox_plan["host_root_readonly"])
        self.assertTrue(sandbox_plan["clearenv"])
        self.assertEqual(["/"], sandbox_plan["readonly_binds"])
        self.assertIn(str(project_root / "graph"), sandbox_plan["masked_paths"])
        self.assertEqual(str(run_dir / "home"), sandbox_plan["home_dir"])
        self.assertEqual(str(run_dir / "state" / "pi-agent"), sandbox_plan["pi_agent_dir"])
        self.assertEqual(str(run_dir / "state" / "pi-sessions"), sandbox_plan["pi_session_dir"])
        self.assertIn("HOME", sandbox_plan["env_keys"])
        self.assertIn("PI_CODING_AGENT_DIR", sandbox_plan["env_keys"])
        self.assertIn("CUSTOM_RUNTIME_ENV", sandbox_plan["runtime_env_keys"])

    def test_command_log_redacts_secret_arguments(self) -> None:
        command = [
            "bwrap",
            "--setenv",
            "BLUEPRINT_DEEPSEEK_API_KEY",
            "sk-secret",
            "--setenv",
            "PATH",
            "/usr/bin",
            "pi",
            "--api-key",
            "sk-runtime",
            "TOKEN=value",
            "--password=hidden",
        ]

        redacted = _redact_command_for_log(command)

        self.assertNotIn("sk-secret", redacted)
        self.assertNotIn("sk-runtime", redacted)
        self.assertNotIn("TOKEN=value", redacted)
        self.assertNotIn("--password=hidden", redacted)
        self.assertIn("BLUEPRINT_DEEPSEEK_API_KEY", redacted)
        self.assertIn("/usr/bin", redacted)
        self.assertEqual("[REDACTED]", redacted[3])
        self.assertEqual("[REDACTED]", redacted[9])

    def test_command_adapter_requires_bwrap_when_sandbox_enabled(self) -> None:
        project_root = self.project_service.project_path("test-project")
        run_id = "run_bwrap_required_check"
        run_dir = project_root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        store = self.project_service.graph_store("test-project")
        card = next(item for item in store.load_cards() if item.card_id == "card_enrichment_group")
        packet = self.worker._task_packet("test-project", run_id, card, store.load_graph().assets, "shell")
        packet_path = run_dir / "task_packet.json"
        packet_path.write_text(json.dumps(packet.model_dump(), ensure_ascii=True), encoding="utf-8")

        adapter = ShellWorkerAdapter()
        self.project_service.settings.executor_sandbox_mode = "bwrap"
        with patch("app.workers.command_worker._ensure_bwrap_runtime", side_effect=RuntimeError("bwrap required")):
            with self.assertRaises(RuntimeError) as context:
                adapter.build_launch_spec(
                    packet=packet,
                    packet_path=packet_path,
                    run_dir=run_dir,
                    project_root=project_root,
                    settings=self.project_service.settings,
                )
        self.assertIn("bwrap required", str(context.exception))

    def test_sandboxed_execution_guard_allows_different_cards_in_parallel(self) -> None:
        first_guard, first_kind = self.worker._acquire_execution_guard("test-project", "card_one", sandboxed=True)
        second_guard, second_kind = self.worker._acquire_execution_guard("test-project", "card_two", sandboxed=True)
        duplicate_guard, _duplicate_kind = self.worker._acquire_execution_guard("test-project", "card_one", sandboxed=True)
        try:
            self.assertIsNotNone(first_guard)
            self.assertIsNotNone(second_guard)
            self.assertEqual("composite", first_kind)
            self.assertEqual("composite", second_kind)
            self.assertIsNone(duplicate_guard)
        finally:
            if first_guard is not None:
                self.worker._release_execution_guard(first_guard, first_kind)
            if second_guard is not None:
                self.worker._release_execution_guard(second_guard, second_kind)

    def test_real_agent_adapter_launches_via_wrapper(self) -> None:
        settings = self.project_service.settings
        original_command = settings.opencode_command
        original_sandbox_mode = settings.executor_sandbox_mode
        settings.opencode_command = "opencode run {executor_prompt_path}"
        settings.executor_sandbox_mode = "bwrap"
        project_root = self.project_service.project_path("test-project")
        run_id = "run_wrapper_check"
        run_dir = project_root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        store = self.project_service.graph_store("test-project")
        card = next(item for item in store.load_cards() if item.card_id == "card_enrichment_group")
        packet = self.worker._task_packet("test-project", run_id, card, store.load_graph().assets, "opencode")
        packet_path = run_dir / "task_packet.json"
        packet_path.write_text(json.dumps(packet.model_dump(), ensure_ascii=True), encoding="utf-8")
        try:
            with patch("app.workers.command_worker._ensure_bwrap_runtime", return_value="/usr/bin/bwrap"):
                spec = self.worker.registry["opencode"].build_launch_spec(
                    packet=packet,
                    packet_path=packet_path,
                    run_dir=run_dir,
                    project_root=project_root,
                    settings=settings,
                )
            self.assertIn("app.workers.agent_cli_executor", " ".join(spec.command))
            self.assertEqual("opencode", spec.environment["BLUEPRINT_AGENT_PROVIDER"])
            self.assertEqual("opencode run {executor_prompt_path}", spec.environment["BLUEPRINT_AGENT_LAUNCH_TEMPLATE"])
            self.assertIn("BLUEPRINT_AGENT_PROVIDER", spec.command)
            self.assertIn("BLUEPRINT_AGENT_LAUNCH_TEMPLATE", spec.command)
        finally:
            settings.opencode_command = original_command
            settings.executor_sandbox_mode = original_sandbox_mode

    def test_agent_cli_wrapper_repairs_invalid_manifest_candidate(self) -> None:
        settings = self.project_service.settings
        original_command = settings.opencode_command
        project_root = self.project_service.project_path("test-project")
        run_id = "run_wrapper_repair_check"
        run_dir = project_root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        store = self.project_service.graph_store("test-project")
        card = next(item for item in store.load_cards() if item.card_id == "card_enrichment_group")
        packet = self.worker._task_packet("test-project", run_id, card, store.load_graph().assets, "opencode")
        packet_path = run_dir / "task_packet.json"
        packet_path.write_text(json.dumps(packet.model_dump(), ensure_ascii=True), encoding="utf-8")
        stub_path = Path(self.tmpdir) / "agent_manifest_stub.py"
        stub_path.write_text(
            """
import json
import os
import sys
from pathlib import Path

packet = json.loads(Path(os.environ["BLUEPRINT_TASK_PACKET"]).read_text(encoding="utf-8"))
run_dir = Path(os.environ["BLUEPRINT_RUN_DIR"])
project_root = Path(os.environ["BLUEPRINT_PROJECT_ROOT"])
candidate = Path(os.environ["BLUEPRINT_MANIFEST_CANDIDATE_PATH"])
repair_prompt = os.environ.get("BLUEPRINT_MANIFEST_REPAIR_PROMPT")
(run_dir / "seen_prompts.log").open("a", encoding="utf-8").write(Path(sys.argv[1]).name + "\\n")
created_assets = []
for item in packet["expected_outputs"]:
    output_path = project_root / item["path_hint"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"generated:{item['role']}\\n", encoding="utf-8")
    created_assets.append({"role": item["role"], "path": item["path_hint"]})
code_path = project_root / "scripts" / "generated" / packet["task_id"] / "analysis.py"
code_path.parent.mkdir(parents=True, exist_ok=True)
code_path.write_text("print('repair test')\\n", encoding="utf-8")
if repair_prompt:
    manifest = {
        "run_id": packet["task_id"],
        "status": "success",
        "summary": "Repaired manifest is valid.",
        "inputs_used": packet["input_assets"],
        "created_assets": created_assets,
        "code_artifacts": [{"path": str(code_path.relative_to(project_root)), "language": "python"}],
        "commands_executed": ["agent-manifest-stub"],
        "metrics": {},
        "key_findings": [],
        "recommended_graph_updates": [],
        "warnings": [],
    }
else:
    manifest = {
        "run_id": packet["task_id"],
        "status": "completed",
        "inputs_used": packet["input_assets"],
        "outputs": created_assets,
        "code_artifacts": [{"path": str(code_path.relative_to(project_root)), "language": "python"}],
    }
candidate.write_text(json.dumps(manifest), encoding="utf-8")
""".strip()
            + "\n",
            encoding="utf-8",
        )
        settings.opencode_command = f"{sys.executable} {stub_path} {{executor_prompt_path}}"
        try:
            spec = self.worker.registry["opencode"].build_launch_spec(
                packet=packet,
                packet_path=packet_path,
                run_dir=run_dir,
                project_root=project_root,
                settings=settings,
            )
            env = {
                **spec.environment,
                "BLUEPRINT_TASK_PACKET": str(packet_path),
                "BLUEPRINT_RUN_DIR": str(run_dir),
                "BLUEPRINT_PROJECT_ROOT": str(project_root),
                "BLUEPRINT_MANIFEST_CANDIDATE_PATH": str(run_dir / "manifest.candidate.json"),
            }
            result = subprocess.run(spec.command, cwd=spec.cwd, env=env, text=True, capture_output=True, check=False)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            manifest = Manifest.model_validate(json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")))
            trace = json.loads((run_dir / "agent_trace.json").read_text(encoding="utf-8"))
            self.assertEqual("success", manifest.status)
            self.assertEqual("Repaired manifest is valid.", manifest.summary)
            self.assertEqual("success", trace["status"])
            self.assertEqual("opencode", trace["provider"])
            self.assertGreaterEqual(len(trace["provider_attempts"]), 2)
            self.assertTrue((run_dir / "agent_output_timeline.jsonl").exists())
            timeline_events = [
                json.loads(line)
                for line in (run_dir / "agent_output_timeline.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(any(item["event_type"] == "process_start" for item in timeline_events))
            self.assertTrue(any(item["event_type"] == "process_exit" for item in timeline_events))
            self.assertTrue(any(item["phase"] == "manifest_repair" for item in trace["provider_attempts"]))
            self.assertTrue(any(item["status"] == "failed" for item in trace["manifest_validation"]))
            self.assertTrue(any(item["path"].endswith("manifest.json") for item in trace["file_timeline"]))
            self.assertTrue((run_dir / "manifest_repair_prompt.md").exists())
            self.assertIn("status must be exactly one of", (run_dir / "manifest_repair_prompt.md").read_text(encoding="utf-8"))
            self.assertEqual(
                ["executor_prompt.md", "manifest_repair_prompt.md"],
                (run_dir / "seen_prompts.log").read_text(encoding="utf-8").splitlines(),
            )
        finally:
            settings.opencode_command = original_command

    def test_agent_cli_wrapper_forwards_input_conclusion_to_review_context(self) -> None:
        settings = self.project_service.settings
        original_command = settings.opencode_command
        project_root = self.project_service.project_path("test-project")
        run_id = "run_wrapper_input_conclusion"
        run_dir = project_root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        store = self.project_service.graph_store("test-project")
        card = next(item for item in store.load_cards() if item.card_id == "card_enrichment_group")
        packet = self.worker._task_packet("test-project", run_id, card, store.load_graph().assets, "opencode")
        packet_path = run_dir / "task_packet.json"
        packet_path.write_text(json.dumps(packet.model_dump(), ensure_ascii=True), encoding="utf-8")
        stub_path = Path(self.tmpdir) / "agent_manifest_missing_inputs_stub.py"
        stub_path.write_text(
            """
import json
import os
import sys
from pathlib import Path

packet = json.loads(Path(os.environ["BLUEPRINT_TASK_PACKET"]).read_text(encoding="utf-8"))
run_dir = Path(os.environ["BLUEPRINT_RUN_DIR"])
project_root = Path(os.environ["BLUEPRINT_PROJECT_ROOT"])
candidate = Path(os.environ["BLUEPRINT_MANIFEST_CANDIDATE_PATH"])

created_assets = []
for item in packet["expected_outputs"]:
    output_path = project_root / item["path_hint"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"generated:{item['role']}\\n", encoding="utf-8")
    created_assets.append({"role": item["role"], "path": item["path_hint"]})
code_path = project_root / "scripts" / "generated" / packet["task_id"] / "analysis.py"
code_path.parent.mkdir(parents=True, exist_ok=True)
code_path.write_text("print('repair test')\\n", encoding="utf-8")
manifest = {
    "run_id": packet["task_id"],
    "status": "success",
    "summary": "Manifest missing some inputs on first pass.",
    "created_assets": created_assets,
    "validation_evidence": {
        "input_conclusion": "Reviewed the declared inputs and used the relevant ones for this analysis.",
    },
    "code_artifacts": [{"path": str(code_path.relative_to(project_root)), "language": "python"}],
    "commands_executed": ["agent-manifest-stub"],
    "metrics": {},
    "key_findings": [],
    "recommended_graph_updates": [],
    "warnings": [],
}
candidate.write_text(json.dumps(manifest), encoding="utf-8")
""".strip()
            + "\n",
            encoding="utf-8",
        )
        settings.opencode_command = f"{sys.executable} {stub_path} {{executor_prompt_path}}"
        try:
            spec = self.worker.registry["opencode"].build_launch_spec(
                packet=packet,
                packet_path=packet_path,
                run_dir=run_dir,
                project_root=project_root,
                settings=settings,
            )
            env = {
                **spec.environment,
                "BLUEPRINT_TASK_PACKET": str(packet_path),
                "BLUEPRINT_RUN_DIR": str(run_dir),
                "BLUEPRINT_PROJECT_ROOT": str(project_root),
                "BLUEPRINT_MANIFEST_CANDIDATE_PATH": str(run_dir / "manifest.candidate.json"),
            }
            result = subprocess.run(spec.command, cwd=spec.cwd, env=env, text=True, capture_output=True, check=False)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            manifest = Manifest.model_validate(json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")))
            self.assertEqual("success", manifest.status)
            self.assertEqual("Reviewed the declared inputs and used the relevant ones for this analysis.", manifest.validation_evidence["input_conclusion"])
            self.assertFalse((run_dir / "manifest_repair_prompt.md").exists())
            prompt_text = (run_dir / "executor_prompt.md").read_text(encoding="utf-8")
            self.assertIn("validation_evidence.input_conclusion", prompt_text)
            self.assertIn("Do not restate the full input list in the manifest", prompt_text)
            context = self.manifest_service.manifest_to_review_context("test-project", run_id)
            self.assertEqual(len(packet.input_assets), len(context.declared_input_assets))
            self.assertEqual("Reviewed the declared inputs and used the relevant ones for this analysis.", context.input_conclusion)
        finally:
            settings.opencode_command = original_command

    def test_project_snapshot_exposes_worker_capabilities(self) -> None:
        settings = self.project_service.settings
        original_command = settings.opencode_command
        settings.opencode_command = "bash /absolute/path/to/opencode-launch.sh {executor_prompt_path}"
        try:
            snapshot = self.project_service.get_project_snapshot("test-project")
            opencode = next(item for item in snapshot["worker_capabilities"] if item["worker_type"] == "opencode")
            self.assertTrue(opencode["configured"])
            self.assertEqual("agent_cli_wrapper", opencode["execution_mode"])
            self.assertEqual("opencode_command", opencode["launch_template_setting"])
            self.assertTrue(opencode["recommended_launch_examples"])
            self.assertTrue(opencode["notes"])
        finally:
            settings.opencode_command = original_command

    def test_patch_apply_updates_parent_group_when_submodule_status_changes(self) -> None:
        group_module_id, child_module_id, group_card_id, _child_card_id = self._add_single_submodule_group_fixture()
        patch = GraphPatch.model_validate(
            {
                "patch_id": "patch_group_running",
                "patch_type": "update_card",
                "source": "manager_ai",
                "reason": "Promote child module to running.",
                "ops": [
                    {"op": "set_module_status", "payload": {"module_id": child_module_id, "status": "running"}},
                ],
            }
        )

        self.apply.apply_patch("test-project", patch)

        snapshot = self.project_service.get_project_snapshot("test-project")
        group_card = next(item for item in snapshot["cards"] if item.card_id == group_card_id)
        group_module = next(item for item in snapshot["graph"].modules if item.module_id == group_module_id)
        child_card = next(item for item in snapshot["cards"] if item.card_id == "card_runtime_child")
        self.assertEqual("running", child_card.status)
        self.assertEqual("running", group_card.status)
        self.assertEqual("has_running", group_card.aggregate_status)
        self.assertEqual("running", group_module.submodules[0].status)

    def test_start_run_updates_parent_group_when_child_card_enters_running(self) -> None:
        _group_module_id, child_module_id, group_card_id, child_card_id = self._add_single_submodule_group_fixture()
        self.worker.registry["needs_approval_stub"] = NeedsApprovalWorkerAdapter()

        run = self.worker.start_run("test-project", child_card_id, worker_type="needs_approval_stub")

        self.assertEqual("needs_approval", run["status"])
        snapshot = self.project_service.get_project_snapshot("test-project")
        group_card = next(item for item in snapshot["cards"] if item.card_id == group_card_id)
        child_module = next(item for item in snapshot["graph"].modules if item.module_id == child_module_id)
        self.assertEqual("running", group_card.status)
        self.assertEqual("has_running", group_card.aggregate_status)
        self.assertEqual("running", child_module.status)

    def test_review_run_updates_single_submodule_group_to_accepted(self) -> None:
        _group_module_id, child_module_id, group_card_id, child_card_id = self._add_single_submodule_group_fixture()

        run = self.worker.start_run("test-project", child_card_id, worker_type="pi")
        self._wait_for_run("test-project", run["run_id"])
        self.worker.review_run("test-project", run["run_id"], accept=True)

        snapshot = self.project_service.get_project_snapshot("test-project")
        group_card = next(item for item in snapshot["cards"] if item.card_id == group_card_id)
        child_module = next(item for item in snapshot["graph"].modules if item.module_id == child_module_id)
        self.assertEqual("accepted", child_module.status)
        self.assertEqual("accepted", group_card.status)
        self.assertEqual("all_accepted", group_card.aggregate_status)

    def test_agent_cli_wrapper_forwards_provider_events_and_succeeds(self) -> None:
        settings = self.project_service.settings
        original_command = settings.opencode_command
        project_root = self.project_service.project_path("test-project")
        script_path = project_root / "scripts" / "generated" / "opencode_provider_stub.py"
        script_path.write_text(
            """
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
packet_path = Path(sys.argv[2])
project_root = Path(sys.argv[3])
packet = json.loads(packet_path.read_text(encoding="utf-8"))
code_dir = project_root / "scripts" / "generated" / packet["task_id"]
code_dir.mkdir(parents=True, exist_ok=True)
code_path = code_dir / "provider_stub.py"
code_path.write_text(
    f"INPUTS = {json.dumps([item['path'] for item in packet['input_assets']], ensure_ascii=True)!r}\\n"
    f"OUTPUTS = {json.dumps([item['path_hint'] for item in packet['expected_outputs']], ensure_ascii=True)!r}\\n",
    encoding="utf-8",
)
created_assets = []
for item in packet["expected_outputs"]:
    output_path = project_root / item["path_hint"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"generated:{item['role']}\\n", encoding="utf-8")
    created_assets.append(
        {
            "role": item["role"],
            "path": item["path_hint"],
            "description": f"generated {item['role']}",
        }
    )
print('BP_EVENT {"type":"progress_update","stage":"provider","progress":55,"message":"provider stub running"}', flush=True)
print(
    "BP_EVENT "
    + json.dumps(
        {
            "type": "final_report",
            "summary": "Provider wrapper completed.",
            "key_findings": ["provider stub emitted structured events"],
            "warnings": [],
        },
        ensure_ascii=False,
    ),
    flush=True,
)
manifest = {
    "run_id": packet["task_id"],
    "status": "success",
    "summary": "Provider manifest complete.",
    "inputs_used": packet["input_assets"],
    "created_assets": created_assets,
    "code_artifacts": [{"path": str(code_path.relative_to(project_root)), "language": "python"}],
    "commands_executed": ["provider-stub"],
    "metrics": {},
    "key_findings": ["provider stub emitted structured events"],
    "recommended_graph_updates": [],
    "warnings": [],
}
manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
""".strip()
            + "\n",
            encoding="utf-8",
        )
        settings.opencode_command = f"{{python}} {script_path} {{manifest_path}} {{task_packet_path}} {{project_root}}"
        try:
            run = self.worker.start_run("test-project", "card_enrichment_group", worker_type="opencode")
            self._wait_for_run("test-project", run["run_id"])
            events = self.project_service.graph_store("test-project").load_run_events(run["run_id"])
            self.assertTrue(any(event.event_type == "executor_progress" and event.message == "provider stub running" for event in events))
            self.assertTrue(any(event.event_type == "executor_final_report" and event.message == "Provider wrapper completed." for event in events))
            trace_path = self.project_service.project_path("test-project") / "runs" / run["run_id"] / "agent_trace.json"
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            self.assertEqual("success", trace["status"])
            self.assertEqual("initial_provider", trace["provider_attempts"][0]["phase"])
            self.assertEqual(0, trace["provider_attempts"][0]["exit_code"])
            self.assertGreater(trace["provider_attempts"][0]["stdout_line_count"], 0)
            self.assertTrue(any(item["path"].endswith("manifest.json") for item in trace["file_timeline"]))
            snapshot = self.project_service.get_project_snapshot("test-project")
            run_record = next(item for item in snapshot["graph"].runs if item.run_id == run["run_id"])
            self.assertEqual("reviewed", run_record.status)
            self.assertEqual("Provider wrapper completed.", run_record.summary)
        finally:
            settings.opencode_command = original_command

    def test_agent_cli_wrapper_fails_when_manifest_is_missing(self) -> None:
        settings = self.project_service.settings
        original_command = settings.opencode_command
        project_root = self.project_service.project_path("test-project")
        script_path = project_root / "scripts" / "generated" / "opencode_manifest_missing.py"
        script_path.write_text(
            """
print('BP_EVENT {"type":"progress_update","stage":"provider","progress":10,"message":"provider exited without manifest"}', flush=True)
""".strip()
            + "\n",
            encoding="utf-8",
        )
        settings.opencode_command = f"{{python}} {script_path}"
        try:
            run = self.worker.start_run("test-project", "card_enrichment_group", worker_type="opencode")
            self._wait_for_run("test-project", run["run_id"])
            snapshot = self.project_service.get_project_snapshot("test-project")
            run_record = next(item for item in snapshot["graph"].runs if item.run_id == run["run_id"])
            self.assertEqual("failed", run_record.status)
            transcript = (
                self.project_service.project_path("test-project") / "runs" / run["run_id"] / "transcript.md"
            ).read_text(encoding="utf-8")
            self.assertIn("manifest.json is missing", transcript)
        finally:
            settings.opencode_command = original_command

    def test_network_denied_executor_context_blocks_real_agent_worker(self) -> None:
        settings = self.project_service.settings
        original_command = settings.opencode_command
        settings.opencode_command = (
            "{python} -m app.workers.demo_executor --task-packet {task_packet_path} --run-dir {run_dir} --project-root {project_root}"
        )
        store = self.project_service.graph_store("test-project")
        cards = store.load_cards()
        card = next(item for item in cards if item.card_id == "card_enrichment_group")
        card.executor_context = ExecutorContext.model_validate(
            {
                "executor_profile": "real_agent",
                "tool_policy": {"network": "deny", "python": True, "rscript": True, "shell": True, "git_write": False},
            }
        )
        store.save_cards(cards)
        try:
            with self.assertRaises(HTTPException) as ctx:
                self.worker.start_run("test-project", "card_enrichment_group", worker_type="opencode")
            self.assertEqual(409, ctx.exception.status_code)
            self.assertIn("network=deny", str(ctx.exception.detail))
        finally:
            settings.opencode_command = original_command

    def test_network_allow_executor_context_skips_runtime_network_approval(self) -> None:
        settings = self.project_service.settings
        original_command = settings.opencode_command
        settings.opencode_command = (
            "{python} -m app.workers.demo_executor --task-packet {task_packet_path} --run-dir {run_dir} --project-root {project_root}"
        )
        store = self.project_service.graph_store("test-project")
        cards = store.load_cards()
        card = next(item for item in cards if item.card_id == "card_enrichment_group")
        card.executor_context = ExecutorContext.model_validate(
            {
                "executor_profile": "real_agent",
                "tool_policy": {"network": "allow", "python": True, "rscript": True, "shell": True, "git_write": False},
            }
        )
        store.save_cards(cards)
        try:
            run = self.worker.start_run("test-project", "card_enrichment_group", worker_type="opencode")
            self.assertEqual("queued", run["status"])
            decisions = self.runtime_approval_service.load_decisions("test-project", run["run_id"])
            self.assertFalse(any(item["action"] == "network" for item in decisions))
            self._wait_for_run("test-project", run["run_id"])
        finally:
            settings.opencode_command = original_command

    def test_manifest_collision_is_reported_by_validator(self) -> None:
        project_root = self.project_service.project_path("test-project")
        store = self.project_service.graph_store("test-project")
        graph = store.load_graph()
        card = next(item for item in store.load_cards() if item.card_id == "card_enrichment_group")
        run_id = "run_manifest_collision"
        run_dir = project_root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        packet = self.worker._task_packet("test-project", run_id, card, graph.assets, "shell")
        packet_path = run_dir / "task_packet.json"
        packet_path.write_text(json.dumps(packet.model_dump(), ensure_ascii=True), encoding="utf-8")

        colliding_output = packet.expected_outputs[0]
        graph.assets.append(
            Asset(
                asset_id="existing_valid_collision",
                asset_type=colliding_output.artifact_class,
                title="Existing valid output",
                status="valid",
                created_by_run="run_previous",
                path=colliding_output.path_hint,
                summary="Pre-existing valid asset occupying the same path.",
            )
        )
        store.save_graph(graph)
        output_path = project_root / colliding_output.path_hint
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("collision\n", encoding="utf-8")

        manifest = {
            "run_id": run_id,
            "status": "success",
            "summary": "collision test",
            "inputs_used": packet.model_dump()["input_assets"],
            "created_assets": [
                {
                    "role": item.role,
                    "path": item.path_hint,
                    "description": f"generated {item.role}",
                }
                for item in packet.expected_outputs
            ],
            "commands_executed": ["collision-test"],
            "metrics": {},
            "key_findings": [],
            "recommended_graph_updates": [],
            "warnings": [],
        }
        for item in packet.expected_outputs[1:]:
            absolute = project_root / item.path_hint
            absolute.parent.mkdir(parents=True, exist_ok=True)
            absolute.write_text(f"generated:{item.role}\n", encoding="utf-8")
        (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        ok, errors = self.manifest_service.validate_manifest("test-project", run_id)
        self.assertFalse(ok)
        self.assertTrue(any("collides with an existing valid asset" in error for error in errors))

    def test_filesystem_audit_ignores_backend_chat_session_writes(self) -> None:
        project_root = self.project_service.project_path("test-project")
        store = self.project_service.graph_store("test-project")
        graph = store.load_graph()
        card = next(item for item in store.load_cards() if item.card_id == "card_enrichment_group")
        run_id = "run_audit_chat_sessions"
        run_dir = project_root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        packet = self.worker._task_packet("test-project", run_id, card, graph.assets, "shell")
        atomic_write_json(run_dir / "task_packet.json", packet.model_dump())
        before = self.manifest_service.capture_filesystem_snapshot("test-project")

        chat_sessions = project_root / "chat" / "sessions.json"
        chat_sessions.write_text('[{"session_id":"session_audit","summary":"updated"}]\n', encoding="utf-8")
        output = project_root / packet.expected_outputs[0].path_hint
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("ok\n", encoding="utf-8")

        ok, violations, changes = self.manifest_service.audit_run_filesystem("test-project", run_id, before)

        self.assertTrue(ok, violations)
        self.assertEqual([], violations)
        self.assertTrue(any(item["path"] == "chat/sessions.json" for item in changes))

    def test_project_rejects_parallel_run_start_to_keep_filesystem_audit_scoped(self) -> None:
        script = """
import json
import sys
import time
from pathlib import Path

packet = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
run_dir = Path(sys.argv[2])
project_root = Path(sys.argv[3])
created_assets = []
for item in packet["expected_outputs"]:
    absolute = project_root / item["path_hint"]
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text(f"generated:{item['role']}\\n", encoding="utf-8")
    created_assets.append(
        {
            "role": item["role"],
            "path": item["path_hint"],
            "description": f"generated {item['role']}",
        }
    )
time.sleep(0.5)
manifest = {
    "run_id": packet["task_id"],
    "status": "success",
    "summary": f"{packet['task_id']} complete",
    "inputs_used": packet["input_assets"],
    "created_assets": created_assets,
    "code_artifacts": [],
    "commands_executed": ["serial-audit-test"],
    "metrics": {},
    "key_findings": [],
    "recommended_graph_updates": [],
    "warnings": [],
}
(run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
"""
        self.worker.registry["serial_audit_stub"] = InlineCommandWorkerAdapter("serial_audit_stub", script)
        _group_module_id, _child_module_id, _group_card_id, child_card_id = self._add_single_submodule_group_fixture()
        original_sandbox_mode = self.project_service.settings.executor_sandbox_mode
        self.project_service.settings.executor_sandbox_mode = "none"
        try:
            first = self.worker.start_run("test-project", "card_enrichment_group", worker_type="serial_audit_stub")
            with self.assertRaises(HTTPException) as context:
                self.worker.start_run("test-project", child_card_id, worker_type="serial_audit_stub")
            self.assertEqual(409, context.exception.status_code)

            self._wait_for_run("test-project", first["run_id"])
            second = self.worker.start_run("test-project", child_card_id, worker_type="serial_audit_stub")
            self._wait_for_run("test-project", second["run_id"])
        finally:
            self.project_service.settings.executor_sandbox_mode = original_sandbox_mode

        project_root = self.project_service.project_path("test-project")
        for run in [first, second]:
            audit = json.loads((project_root / "runs" / run["run_id"] / "filesystem_audit.json").read_text(encoding="utf-8"))
            self.assertEqual([], audit["violations"])
            if run == second:
                self.assertFalse(any(f"runs/{first['run_id']}/" in item["path"] for item in audit["changes"]))

    def test_post_run_validation_exception_marks_run_failed(self) -> None:
        script = """
import json
import sys
from pathlib import Path

packet = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
run_dir = Path(sys.argv[2])
project_root = Path(sys.argv[3])
code_path = project_root / "scripts" / "generated" / packet["task_id"] / "analysis.py"
code_path.parent.mkdir(parents=True, exist_ok=True)
code_path.write_text("print('analysis')\\n", encoding="utf-8")
created_assets = []
for item in packet["expected_outputs"]:
    absolute = project_root / item["path_hint"]
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text(f"generated:{item['role']}\\n", encoding="utf-8")
    created_assets.append(
        {
            "role": item["role"],
            "path": item["path_hint"],
            "description": f"generated {item['role']}",
        }
    )
manifest = {
    "run_id": packet["task_id"],
    "status": "success",
    "summary": "validation exception test",
    "inputs_used": packet["input_assets"],
    "created_assets": created_assets,
    "code_artifacts": [{"path": f"scripts/generated/{packet['task_id']}/analysis.py", "language": "python"}],
    "commands_executed": ["validation-exception-test"],
    "metrics": {},
    "key_findings": [],
    "recommended_graph_updates": [],
    "warnings": [],
}
(run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
"""
        self.worker.registry["validation_exception_stub"] = InlineCommandWorkerAdapter("validation_exception_stub", script)
        original_validation_service = self.worker.executor_validation_service
        self.worker.executor_validation_service = RaisingValidationService()
        try:
            run = self.worker.start_run("test-project", "card_enrichment_group", worker_type="validation_exception_stub")
            self._wait_for_run("test-project", run["run_id"])
        finally:
            self.worker.executor_validation_service = original_validation_service

        snapshot = self.project_service.get_project_snapshot("test-project")
        run_record = next(item for item in snapshot["graph"].runs if item.run_id == run["run_id"])
        self.assertEqual("failed", run_record.status)
        self.assertIn("执行器运行后处理失败", run_record.summary)
        events = self.project_service.graph_store("test-project").load_run_events(run["run_id"])
        self.assertTrue(any(event.event_type == "run_failed" and "validator exploded" in event.message for event in events))

    def test_timeout_with_complete_manifest_candidate_continues_to_review(self) -> None:
        script = """
import json
import sys
import time
from pathlib import Path

packet = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
run_dir = Path(sys.argv[2])
project_root = Path(sys.argv[3])
code_path = project_root / "scripts" / "generated" / packet["task_id"] / "analysis.py"
code_path.parent.mkdir(parents=True, exist_ok=True)
code_path.write_text("print('analysis complete')\\n", encoding="utf-8")
created_assets = []
for item in packet["expected_outputs"]:
    path = project_root / item["path_hint"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"generated:{item['role']}\\n", encoding="utf-8")
    created_assets.append(
        {
            "role": item["role"],
            "path": item["path_hint"],
            "label": item.get("label"),
            "description": f"generated {item['role']}",
        }
    )
manifest = {
    "run_id": packet["task_id"],
    "status": "success",
    "summary": "complete before timeout",
    "inputs_used": packet["input_assets"],
    "created_assets": created_assets,
    "code_artifacts": [{"path": f"scripts/generated/{packet['task_id']}/analysis.py", "language": "python"}],
    "commands_executed": ["timeout-recovery-test"],
    "metrics": {},
    "key_findings": ["completed before wrapper timeout"],
    "recommended_graph_updates": [],
    "warnings": [],
}
(run_dir / "manifest.candidate.json").write_text(json.dumps(manifest), encoding="utf-8")
(run_dir / "manager_brief.json").write_text(json.dumps({"final_report": {"summary": "complete before timeout"}}), encoding="utf-8")
print("complete")
time.sleep(5)
"""
        self.worker.registry["timeout_recovery_stub"] = InlineCommandWorkerAdapter("timeout_recovery_stub", script)
        settings = get_settings()
        settings.worker_timeout_seconds = 1

        run = self.worker.start_run("test-project", "card_enrichment_group", worker_type="timeout_recovery_stub")
        self._wait_for_run("test-project", run["run_id"])

        project_root = self.project_service.project_path("test-project")
        self.assertTrue((project_root / "runs" / run["run_id"] / "manifest.json").exists())
        snapshot = self.project_service.get_project_snapshot("test-project")
        run_record = next(item for item in snapshot["graph"].runs if item.run_id == run["run_id"])
        self.assertEqual("reviewed", run_record.status)
        events = self.project_service.graph_store("test-project").load_run_events(run["run_id"])
        self.assertTrue(any(event.event_type == "timeout_manifest_recovered" for event in events))

    def test_agent_cli_wrapper_syncs_run_local_generated_scripts_before_manifest_validation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wrapper-script-sync-") as tmpdir:
            project_root = Path(tmpdir)
            run_id = "run_script_sync"
            run_dir = project_root / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            (project_root / "results" / "card_review" / run_id).mkdir(parents=True, exist_ok=True)
            packet = {
                "task_id": run_id,
                "expected_outputs": [
                    expected_output_spec(
                        "report",
                        f"results/card_review/{run_id}/report.md",
                        artifact_class="document",
                        accepted_formats=["md"],
                    )
                ],
            }
            atomic_write_json(run_dir / "task_packet.json", packet)
            (project_root / "results" / "card_review" / run_id / "report.md").write_text("# report\n", encoding="utf-8")
            local_script = run_dir / "scripts" / "generated" / run_id / "analysis.py"
            local_script.parent.mkdir(parents=True, exist_ok=True)
            local_script.write_text("print('run local script')\n", encoding="utf-8")
            atomic_write_json(
                run_dir / "manifest.candidate.json",
                {
                    "run_id": run_id,
                    "status": "success",
                    "summary": "run-local script only",
                    "created_assets": [
                        {
                            "role": "report",
                            "path": f"results/card_review/{run_id}/report.md",
                            "artifact_class": "document",
                            "format": "md",
                        }
                    ],
                    "code_artifacts": [
                        {
                            "path": f"scripts/generated/{run_id}/analysis.py",
                            "language": "python",
                        }
                    ],
                    "commands_executed": [f"python3 runs/{run_id}/scripts/generated/{run_id}/analysis.py"],
                    "validation_evidence": {"input_conclusion": "No external inputs."},
                },
            )

            errors = agent_cli_executor._promote_candidate_manifest(run_dir=run_dir)

            self.assertEqual([], errors)
            synced_script = project_root / "scripts" / "generated" / run_id / "analysis.py"
            self.assertTrue(synced_script.exists())
            self.assertEqual("print('run local script')\n", synced_script.read_text(encoding="utf-8"))
            self.assertTrue((run_dir / "manifest.json").exists())

    def test_filesystem_audit_violation_marks_run_failed(self) -> None:
        script = """
import json
import sys
from pathlib import Path

packet = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
run_dir = Path(sys.argv[2])
project_root = Path(sys.argv[3])
created_assets = []
for item in packet["expected_outputs"]:
    absolute = project_root / item["path_hint"]
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text(f"generated:{item['role']}\\n", encoding="utf-8")
    created_assets.append(
        {
            "role": item["role"],
            "path": item["path_hint"],
            "description": f"generated {item['role']}",
        }
    )
(project_root / "configs" / "audit_leak.txt").write_text("outside allowed paths\\n", encoding="utf-8")
manifest = {
    "run_id": packet["task_id"],
    "status": "success",
    "summary": "audit violation test",
    "inputs_used": packet["input_assets"],
    "created_assets": created_assets,
    "commands_executed": ["audit-test"],
    "metrics": {},
    "key_findings": [],
    "recommended_graph_updates": [],
    "warnings": [],
}
(run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
"""
        self.worker.registry["audit_violation_stub"] = InlineCommandWorkerAdapter("audit_violation_stub", script)
        run = self.worker.start_run("test-project", "card_enrichment_group", worker_type="audit_violation_stub")
        self._wait_for_run("test-project", run["run_id"])

        snapshot = self.project_service.get_project_snapshot("test-project")
        run_record = next(item for item in snapshot["graph"].runs if item.run_id == run["run_id"])
        self.assertEqual("failed", run_record.status)
        self.assertIn("文件系统审计失败", run_record.summary)

    def test_run_events_capture_structured_executor_reports(self) -> None:
        run = self.worker.start_run("test-project", "card_enrichment_group")
        self._wait_for_run("test-project", run["run_id"])

        events = self.project_service.graph_store("test-project").load_run_events(run["run_id"])
        event_types = [event.event_type for event in events]
        self.assertIn("executor_progress", event_types)
        self.assertIn("executor_final_report", event_types)
        self.assertTrue(any(event.event_type == "executor_progress" and event.source == "executor" for event in events))
        manager_brief = self.project_service.project_path("test-project") / "runs" / run["run_id"] / "manager_brief.json"
        self.assertTrue(manager_brief.exists())
        self.assertIn("final_report", manager_brief.read_text(encoding="utf-8"))

        snapshot = self.project_service.get_project_snapshot("test-project")
        run_record = next(item for item in snapshot["graph"].runs if item.run_id == run["run_id"])
        self.assertEqual("Local scaffold worker completed successfully.", run_record.summary)

    def test_cancel_run_from_needs_approval_resets_card_to_planned(self) -> None:
        self.worker.registry["needs_approval_stub"] = NeedsApprovalWorkerAdapter()
        run = self.worker.start_run("test-project", "card_enrichment_group", worker_type="needs_approval_stub")
        self.assertEqual("needs_approval", run["status"])

        cancelled = self.worker.cancel_run("test-project", run["run_id"], reason="Operator cancelled pending approval run.")
        self.assertEqual("cancelled", cancelled["status"])

        snapshot = self.project_service.get_project_snapshot("test-project")
        card = next(item for item in snapshot["cards"] if item.card_id == "card_enrichment_group")
        run_record = next(item for item in snapshot["graph"].runs if item.run_id == run["run_id"])
        self.assertEqual("planned", card.status)
        self.assertEqual("cancelled", run_record.status)

    def test_cleanup_run_archives_rejected_run_and_removes_artifacts(self) -> None:
        run = self.worker.start_run("test-project", "card_enrichment_group")
        self._wait_for_run("test-project", run["run_id"])
        self.worker.review_run("test-project", run["run_id"], accept=False)

        cleaned = self.worker.cleanup_run("test-project", run["run_id"])
        self.assertEqual("completed", cleaned["cleanup_status"])

        snapshot = self.project_service.get_project_snapshot("test-project")
        run_record = next(item for item in snapshot["graph"].runs if item.run_id == run["run_id"])
        self.assertEqual("completed", run_record.cleanup_status)
        self.assertIsNotNone(run_record.archived_at)
        self.assertFalse(any(asset.created_by_run == run["run_id"] for asset in snapshot["graph"].assets))
        self.assertFalse((self.project_service.project_path("test-project") / "runs" / run["run_id"]).exists())
        self.assertFalse((self.project_service.project_path("test-project") / "results" / "card_enrichment_group" / run["run_id"]).exists())

    def test_cleanup_rejects_run_with_valid_assets(self) -> None:
        run = self.worker.start_run("test-project", "card_enrichment_group")
        self._wait_for_run("test-project", run["run_id"])
        self.worker.review_run("test-project", run["run_id"], accept=True)

        with self.assertRaises(HTTPException) as ctx:
            self.worker.cleanup_run("test-project", run["run_id"])

        self.assertEqual(409, ctx.exception.status_code)

    def test_reset_card_run_state_moves_card_back_to_planned(self) -> None:
        store = self.project_service.graph_store("test-project")
        cards = store.load_cards()
        card = next(item for item in cards if item.card_id == "card_enrichment_group")
        card.status = "failed"
        card.progress_note = "failed"
        store.save_cards(cards)

        result = self.worker.reset_card_run_state("test-project", "card_enrichment_group")
        self.assertEqual("planned", result["status"])

        updated = next(item for item in self.project_service.graph_store("test-project").load_cards() if item.card_id == "card_enrichment_group")
        self.assertEqual("planned", updated.status)
        self.assertIsNone(updated.progress_note)

    def test_rerun_card_creates_new_run(self) -> None:
        first = self.worker.start_run("test-project", "card_enrichment_group")
        self._wait_for_run("test-project", first["run_id"])

        second = self.worker.rerun_card("test-project", "card_enrichment_group")
        self.assertNotEqual(first["run_id"], second["run_id"])
        self._wait_for_run("test-project", second["run_id"])

    def test_tool_policy_can_enable_card_tool_audit(self) -> None:
        policy = self.manager.blueprint_tools.set_tool_policy("test-project", {"audit_card_tools": True})
        self.assertEqual({"audit_card_tools": True}, policy["tool_policy"])

        self.manager.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_audited",
                "title": "Audited Card",
                "summary": "Audit should be recorded when enabled.",
                "step": 1,
                "inputs": [{"label": "counts", "asset_id": "count_matrix_v1"}],
                "outputs": [output_contract("result", asset_id="audited_result")],
            },
        )

        graph = self.project_service.graph_store("test-project").load_graph()
        audit_log = graph.metadata.get("card_tool_audit")
        self.assertEqual("create_card", audit_log[-1]["action"])
        self.assertEqual("card_audited", audit_log[-1]["card_id"])

    def test_validator_rejects_missing_ids_for_status_and_summary_ops(self) -> None:
        patch = GraphPatch.model_validate(
            {
                "patch_id": "patch_missing_refs",
                "patch_type": "update_card",
                "source": "manager_ai",
                "reason": "test missing refs",
                "ops": [
                    {"op": "set_card_status", "payload": {"card_id": "card_missing", "status": "planned"}},
                    {"op": "set_module_status", "payload": {"module_id": "module_missing", "status": "planned"}},
                    {"op": "update_module_summary", "payload": {"module_id": "module_missing", "summary": "missing"}},
                ],
            }
        )
        result = self.validator.validate_patch("test-project", patch)
        self.assertFalse(result.valid)
        self.assertTrue(any("Card missing: card_missing" in error for error in result.errors))
        self.assertTrue(any("Module missing: module_missing" in error for error in result.errors))

    def test_validator_allows_update_of_entities_created_earlier_in_same_patch(self) -> None:
        patch = GraphPatch.model_validate(
            {
                "patch_id": "patch_create_then_update",
                "patch_type": "add_module",
                "source": "manager_ai",
                "reason": "test create then update",
                "ops": [
                    {
                        "op": "create_module",
                        "payload": {
                            "module_id": "module_tmp",
                            "title": "临时模块",
                            "status": "planned",
                        },
                    },
                    {
                        "op": "update_module",
                        "payload": {
                            "module_id": "module_tmp",
                            "summary": "模块摘要",
                        },
                    },
                    {
                        "op": "create_card",
                        "payload": {
                            "card_id": "card_tmp",
                            "card_type": "module",
                            "title": "临时卡片",
                            "status": "planned",
                            "summary": "初始摘要",
                            "why": "test",
                            "inputs": [],
                            "outputs": [],
                            "key_findings": [],
                            "manager_review": "待执行。",
                            "next_actions": ["开始执行"],
                            "linked_modules": ["module_tmp"],
                            "linked_runs": [],
                            "linked_assets": [],
                        },
                    },
                    {
                        "op": "update_card",
                        "payload": {
                            "card_id": "card_tmp",
                            "summary": "更新后摘要",
                        },
                    },
                ],
            }
        )
        result = self.validator.validate_patch("test-project", patch)
        self.assertTrue(result.valid, result.errors)

    def test_apply_patch_reports_missing_reference_with_op_name(self) -> None:
        patch = GraphPatch.model_validate(
            {
                "patch_id": "patch_missing_runtime_ref",
                "patch_type": "update_card",
                "source": "manager_ai",
                "reason": "test runtime ref failure",
                "ops": [
                    {"op": "set_card_status", "payload": {"card_id": "card_de_analysis", "status": "planned"}},
                ],
            }
        )
        self.project_service.graph_store("test-project").save_cards([])
        original_validate_patch = self.apply.validator.validate_patch
        self.apply.validator.validate_patch = lambda _project_id, _patch: ValidationResult(valid=True, errors=[], warnings=[])
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self.apply.apply_patch("test-project", patch)
        finally:
            self.apply.validator.validate_patch = original_validate_patch
        self.assertIn("set_card_status", str(ctx.exception))
        self.assertIn("card_de_analysis", str(ctx.exception))

    def test_apply_patch_keeps_mutation_when_git_commit_fails(self) -> None:
        patch = GraphPatch.model_validate(
            {
                "patch_id": "patch_create_direct_card",
                "patch_type": "add_module",
                "source": "manager_ai",
                "reason": "test git failure",
                "ops": [
                    {
                        "op": "create_card",
                        "payload": {
                            "card_id": "card_direct_patch",
                            "card_type": "module",
                            "title": "Direct Patch Card",
                            "status": "planned",
                            "summary": "Card created by patch apply.",
                            "inputs": [],
                            "outputs": [],
                        },
                    }
                ],
            }
        )

        class BrokenGitService:
            def commit(self, _message: str) -> str:
                raise RuntimeError("git is unavailable")

        original_git_service = self.project_service.git_service
        self.project_service.git_service = lambda _project_id: BrokenGitService()
        try:
            result = self.apply.apply_patch("test-project", patch)
        finally:
            self.project_service.git_service = original_git_service

        snapshot = self.project_service.get_project_snapshot("test-project")
        self.assertIsNone(result.commit_hash)
        self.assertTrue(any("git commit failed" in warning for warning in result.warnings))
        self.assertTrue(any(card.card_id == "card_direct_patch" for card in snapshot["cards"]))
        self.assertIn("needs_git_repair", snapshot["graph"].metadata)

    def test_project_list_exposes_corrupted_project(self) -> None:
        corrupt_root = Path(self.tmpdir) / "corrupt-project"
        (corrupt_root / "graph").mkdir(parents=True)
        (corrupt_root / "project.json").write_text("{not json", encoding="utf-8")

        projects = self.project_service.list_projects()

        corrupt = next(item for item in projects if item.project_id == "corrupt-project")
        self.assertEqual("error", corrupt.status)
        self.assertTrue((corrupt_root / "project_recovery_required.json").exists())

    def test_manifest_review_context_surfaces_invalid_paths(self) -> None:
        root = Path(self.tmpdir) / "test-project"
        run_id = "run_invalid_review_context"
        run_dir = root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            run_dir / "task_packet.json",
            TaskPacket(
                task_id=run_id,
                project_id="test-project",
                card_id="card_de_analysis",
                goal="test",
                worker_instructions="test",
            ).model_dump(),
        )
        atomic_write_json(
            run_dir / "manifest.json",
            Manifest(
                run_id=run_id,
                status="success",
                summary="bad path",
                created_assets=[CreatedAsset(role="bad", path="../outside.tsv")],
            ).model_dump(),
        )

        context = self.manifest_service.manifest_to_review_context("test-project", run_id)

        self.assertTrue(any("Invalid created asset path" in item for item in context.validation_errors))

    def test_chat_job_failure_stores_traceback(self) -> None:
        service = ChatJobService(max_workers=1)

        def boom(_project_id: str, _request: ChatRequest) -> object:
            raise RuntimeError("chat exploded")

        job = service.submit("test-project", ChatRequest(message="hello"), boom)
        job.future.result(timeout=2)

        failed = service.get(job.job_id)
        self.assertIsNotNone(failed)
        self.assertEqual("failed", failed.status)
        self.assertIn("Traceback", failed.error or "")
        self.assertIn("chat exploded", failed.error or "")

    def test_go_spec_does_not_match_good_or_going(self) -> None:
        self.assertIsNone(self.manager.tool_layer._resolve_spec("please create a good module"))
        self.assertIsNone(self.manager.tool_layer._resolve_spec("we are going to discuss the plan"))

    def _wait_for_run(self, project_id: str, run_id: str) -> None:
        deadline = time.time() + 5
        while time.time() < deadline:
            snapshot = self.project_service.get_project_snapshot(project_id)
            run = next(item for item in snapshot["graph"].runs if item.run_id == run_id)
            if run.status in {"success", "failed", "cancelled", "reviewed"}:
                return
            time.sleep(0.05)
        self.fail(f"run {run_id} did not finish in time")


class ManagerPlannerCompatibilityTest(unittest.TestCase):
    def test_resolve_legacy_reasoner_model_for_tool_use(self) -> None:
        self.assertEqual(DeepSeekManagerPlanner.resolve_tool_model("deepseek-reasoner"), "deepseek-v4-pro")

    def test_http_error_message_includes_tool_use_guidance(self) -> None:
        message = DeepSeekManagerPlanner._build_http_error_message(
            status_code=400,
            detail='{"error":{"message":"deepseek-reasoner does not support this tool_choice"}}',
            configured_model="deepseek-reasoner",
            resolved_model="deepseek-v4-pro",
        )
        self.assertIn("deepseek-v4-pro", message)
        self.assertIn("tool-use requests require a DeepSeek v4 model", message)


class ExecutorReviewerWorkerTest(unittest.TestCase):
    def test_reviewer_infrastructure_error_is_warning_not_fail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="validator-reviewer-infra-") as tmpdir:
            root = Path(tmpdir)
            run_id = "run_reviewer_infra"
            run_dir = root / "runs" / run_id
            run_dir.mkdir(parents=True)
            (root / "results").mkdir()
            (root / "results" / "output.tsv").write_text("gene\tvalue\nA\t1\n", encoding="utf-8")
            script = root / "scripts" / "generated" / run_id / "analyze.py"
            script.parent.mkdir(parents=True)
            script.write_text("print('analysis')\n", encoding="utf-8")
            atomic_write_json(
                run_dir / "task_packet.json",
                TaskPacket(
                    task_id=run_id,
                    project_id="test-project",
                    card_id="card_review",
                    goal="Review executor output.",
                    worker_instructions="Run analysis.",
                ).model_dump(),
            )
            atomic_write_json(
                run_dir / "manifest.json",
                Manifest(
                    run_id=run_id,
                    status="success",
                    summary="done",
                    created_assets=[CreatedAsset(role="output", path="results/output.tsv")],
                    code_artifacts=[{"path": f"scripts/generated/{run_id}/analyze.py", "language": "python"}],
                ).model_dump(),
            )

            project_service = object.__new__(ProjectService)
            project_service.settings = get_settings()
            project_service.project_path = lambda _project_id: root
            service = ExecutorValidationService(project_service)
            service.reviewer_worker = RaisingReviewerWorker()

            report = service.validate_run("test-project", run_id)

        self.assertEqual("warn", report.status)
        self.assertEqual("reviewer_infrastructure_error", report.reviewer["mode"])
        self.assertTrue(any(issue.code == "reviewer_infrastructure_error" for issue in report.issues))

    def test_validation_merge_manager_brief_writes_reviewer_summary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="validator-brief-reviewer-") as tmpdir:
            path = Path(tmpdir) / "manager_brief.json"
            atomic_write_json(path, {"final_report": "done"})
            report = ExecutorValidationReport(
                status="warn",
                summary="reviewer warning",
                issues=[],
                reviewer={
                    "mode": "reviewer_worker",
                    "verdict": "warn",
                    "summary": "needs more evidence",
                },
            )

            ExecutorValidationService._merge_manager_brief(path, report)

            brief = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("done", brief["final_report"])
        self.assertEqual("reviewer_worker", brief["reviewer"]["mode"])
        self.assertEqual("needs more evidence", brief["reviewer"]["summary"])

    def test_validation_reports_directory_code_artifact_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="validator-dir-artifact-") as tmpdir:
            root = Path(tmpdir)
            run_id = "run_dir_artifact"
            artifact_dir = root / "scripts" / "generated" / run_id
            artifact_dir.mkdir(parents=True)
            output = root / "results" / "output.tsv"
            output.parent.mkdir(parents=True)
            output.write_text("gene\tvalue\nA\t1\n", encoding="utf-8")
            packet = TaskPacket.model_validate(
                {
                    "task_id": run_id,
                    "project_id": "test-project",
                    "card_id": "card_review",
                    "goal": "Review executor output.",
                    "worker_instructions": "Run analysis.",
                }
            )
            manifest = Manifest.model_validate(
                {
                    "run_id": run_id,
                    "status": "success",
                    "summary": "done",
                    "created_assets": [{"role": "output", "path": "results/output.tsv"}],
                    "code_artifacts": [
                        {
                            "path": f"scripts/generated/{run_id}/",
                            "language": "python",
                            "sha256": "not-a-real-hash",
                        }
                    ],
                }
            )
            service = object.__new__(ExecutorValidationService)

            issues = service._deterministic_issues(root, packet, manifest)

        self.assertTrue(any(issue.code == "code_artifact_not_file" for issue in issues))

    def test_analyze_code_artifact_returns_structured_static_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reviewer-code-evidence-") as tmpdir:
            root = Path(tmpdir)
            run_id = "run_code_evidence"
            script = root / "scripts" / "generated" / run_id / "analyze.py"
            script.parent.mkdir(parents=True)
            script.write_text(
                "input_path = 'data/input.tsv'\noutput_path = 'results/output.tsv'\n# placeholder marker\n",
                encoding="utf-8",
            )
            packet = TaskPacket.model_validate(
                {
                    "task_id": run_id,
                    "project_id": "test-project",
                    "card_id": "card_review",
                    "goal": "Review executor output.",
                    "input_assets": [{"asset_id": "input", "path": "data/input.tsv", "type": "table"}],
                    "worker_instructions": "Run analysis.",
                }
            )
            manifest = Manifest.model_validate(
                {
                    "run_id": run_id,
                    "status": "success",
                    "summary": "done",
                    "created_assets": [{"role": "output", "path": "results/output.tsv"}],
                    "code_artifacts": [{"path": f"scripts/generated/{run_id}/analyze.py", "language": "python"}],
                }
            )
            worker = ExecutorReviewerWorker(get_settings())

            result = worker._analyze_code_artifact(root, packet, manifest, f"scripts/generated/{run_id}/analyze.py")

        self.assertTrue(result["ok"])
        self.assertTrue(result["syntax"]["ok"])
        self.assertEqual(["data/input.tsv", "results/output.tsv"], result["referenced_declared_paths"])
        self.assertEqual(["placeholder"], result["suspicious_markers"])

    def test_reviewer_defaults_allow_more_than_twenty_turns(self) -> None:
        worker = ExecutorReviewerWorker(Settings(_env_file=None))

        self.assertGreaterEqual(worker._max_review_turns(), 20)

    def test_reviewer_tools_do_not_expose_python_only_checker(self) -> None:
        tool_names = {tool["name"] for tool in ExecutorReviewerWorker._tools()}

        self.assertNotIn("check_python_code", tool_names)
        self.assertIn("analyze_code_artifact", tool_names)

    def test_read_review_file_uses_larger_window_for_code_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reviewer-code-window-") as tmpdir:
            root = Path(tmpdir)
            run_id = "run_code_window"
            script = root / "scripts" / "generated" / run_id / "analysis.R"
            script.parent.mkdir(parents=True)
            script.write_text("# R script\n" + ("x <- 1\n" * 3000), encoding="utf-8")
            packet = TaskPacket.model_validate(
                {
                    "task_id": run_id,
                    "project_id": "test-project",
                    "card_id": "card_review",
                    "goal": "Review executor output.",
                    "worker_instructions": "Run analysis.",
                }
            )
            manifest = Manifest.model_validate(
                {
                    "run_id": run_id,
                    "status": "success",
                    "summary": "done",
                    "code_artifacts": [{"path": f"scripts/generated/{run_id}/analysis.R", "language": "r"}],
                }
            )
            worker = ExecutorReviewerWorker(Settings(_env_file=None))

            result = worker._read_review_file(root, packet, manifest, f"scripts/generated/{run_id}/analysis.R")

        self.assertTrue(result["ok"])
        self.assertFalse(result["truncated"])
        self.assertGreater(result["preview_limit_bytes"], 16_000)

    def test_reviewer_warns_to_submit_near_turn_limit(self) -> None:
        worker = ExecutorReviewerWorker(
            Settings(_env_file=None, deepseek_api_key=SecretStr("test-key"), reviewer_max_turns=3)
        )
        calls: list[dict[str, Any]] = []

        def fake_post_messages(*, messages: list[dict], tools: list[dict], api_key: str) -> dict:
            calls.append({"messages": copy.deepcopy(messages), "tools": tools, "api_key": api_key})
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "id": f"tool_{len(calls)}",
                        "name": "list_review_files",
                        "input": {},
                    }
                ]
            }

        worker._post_messages = fake_post_messages  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory(prefix="reviewer-final-submit-") as tmpdir:
            result = worker.review(
                root=Path(tmpdir),
                packet=TaskPacket.model_validate(
                    {
                        "task_id": "run_submit_warning",
                        "project_id": "test-project",
                        "card_id": "card_review",
                        "goal": "Review executor output.",
                        "worker_instructions": "Run analysis.",
                    }
                ),
                manifest=Manifest.model_validate({"run_id": "run_submit_warning", "status": "success", "summary": "done"}),
                deterministic_issues=[],
            )

        self.assertEqual("reviewer_worker_max_turns", result["mode"])
        self.assertTrue(
            any(
                "final_submit_required" in json.dumps(call["messages"], ensure_ascii=False)
                for call in calls[1:]
            )
        )

    def test_reviewer_allowed_files_are_limited_to_run_delivery_bundle(self) -> None:
        run_id = "run_review_scope"
        packet = TaskPacket.model_validate(
            {
                "task_id": run_id,
                "project_id": "test-project",
                "card_id": "card_review",
                "goal": "Review executor output.",
                "input_assets": [{"asset_id": "input", "path": "data/input.tsv", "type": "table"}],
                "expected_outputs": [
                    expected_output_spec("output", f"results/card_review/{run_id}/output.tsv")
                ],
                "allowed_paths": [
                    f"runs/{run_id}/",
                    f"results/card_review/{run_id}/",
                    "scripts/generated/",
                ],
                "worker_instructions": "Run analysis.",
            }
        )
        manifest = Manifest.model_validate(
            {
                "run_id": run_id,
                "status": "success",
                "summary": "done",
                "created_assets": [
                    {
                        "role": "output",
                        "path": f"results/card_review/{run_id}/output.tsv",
                    },
                    {"role": "output", "path": "graph/graph.json"},
                    {"role": "undeclared", "path": f"results/card_review/{run_id}/extra.tsv"},
                ],
                "code_artifacts": [
                    {"path": f"scripts/generated/{run_id}/analysis.py", "language": "python"},
                    {"path": "scripts/generated/other_run/analysis.py", "language": "python"},
                    {"path": "backend/app/services/worker_service.py", "language": "python"},
                ],
            }
        )
        worker = ExecutorReviewerWorker(get_settings())

        files = worker._allowed_review_files(packet, manifest)

        self.assertIn("data/input.tsv", files)
        self.assertIn(f"results/card_review/{run_id}/output.tsv", files)
        self.assertIn(f"scripts/generated/{run_id}/analysis.py", files)
        self.assertIn(f"runs/{run_id}/manifest.json", files)
        self.assertIn(f"runs/{run_id}/sandbox_plan.json", files)
        self.assertNotIn("graph/graph.json", files)
        self.assertNotIn(f"results/card_review/{run_id}/extra.tsv", files)
        self.assertNotIn("scripts/generated/other_run/analysis.py", files)
        self.assertNotIn("backend/app/services/worker_service.py", files)
        self.assertNotIn(f"runs/{run_id}/executor_prompt.md", files)

    def test_read_review_file_rejects_manifest_path_outside_delivery_bundle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reviewer-scope-") as tmpdir:
            root = Path(tmpdir)
            (root / "graph").mkdir()
            (root / "graph" / "graph.json").write_text('{"secret": true}\n', encoding="utf-8")
            run_id = "run_review_scope_read"
            packet = TaskPacket.model_validate(
                {
                    "task_id": run_id,
                    "project_id": "test-project",
                    "card_id": "card_review",
                    "goal": "Review executor output.",
                    "expected_outputs": [
                        expected_output_spec("output", f"results/card_review/{run_id}/output.tsv")
                    ],
                    "allowed_paths": [f"runs/{run_id}/", f"results/card_review/{run_id}/"],
                    "worker_instructions": "Run analysis.",
                }
            )
            manifest = Manifest.model_validate(
                {
                    "run_id": run_id,
                    "status": "success",
                    "summary": "done",
                    "created_assets": [{"role": "output", "path": "graph/graph.json"}],
                }
            )
            worker = ExecutorReviewerWorker(get_settings())

            result = worker._handle_tool(
                root,
                packet,
                manifest,
                {
                    "name": "read_review_file",
                    "input": {"path": "graph/graph.json"},
                },
            )

        self.assertFalse(result["ok"])
        self.assertIn("not allowed", result["error"])

    def test_invalid_final_tool_call_returns_protocol_error_then_accepts_retry(self) -> None:
        worker = ExecutorReviewerWorker(get_settings())
        calls: list[dict] = []

        def fake_post_messages(*, messages: list[dict], tools: list[dict], api_key: str) -> dict:
            calls.append({"messages": copy.deepcopy(messages), "tools": tools, "api_key": api_key})
            if len(calls) == 1:
                return {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool_bad",
                            "name": "submit_executor_review",
                            "input": {"verdict": "ok", "summary": 123},
                        }
                    ]
                }
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_good",
                        "name": "submit_executor_review",
                        "input": {
                            "verdict": "pass",
                            "summary": "Code and output evidence satisfy the task.",
                            "issues": [],
                            "repair_hints": [],
                            "inspected_files": ["runs/run_review/manifest.json"],
                        },
                    }
                ]
            }

        worker._post_messages = fake_post_messages  # type: ignore[method-assign]
        original_key = worker.settings.deepseek_api_key
        worker.settings.deepseek_api_key = SecretStr("test-key")
        try:
            root = Path(self._testMethodName)
            packet = {
                "task_id": "run_review",
                "project_id": "test-project",
                "card_id": "card_review",
                "goal": "Review executor output.",
                "worker_instructions": "Run analysis.",
            }
            manifest = {
                "run_id": "run_review",
                "status": "success",
                "summary": "done",
            }
            result = worker.review(
                root=root,
                packet=TaskPacket.model_validate(packet),
                manifest=Manifest.model_validate(manifest),
                deterministic_issues=[],
            )
        finally:
            worker.settings.deepseek_api_key = original_key

        self.assertEqual("pass", result["verdict"])
        self.assertEqual("reviewer_worker", result["mode"])
        self.assertEqual(2, result["turns"])
        retry_message = calls[1]["messages"][-1]["content"][0]["content"]
        self.assertIn("invalid_submit_executor_review_schema", retry_message)
        self.assertIn("schema_errors", retry_message)

    def test_reviewer_writes_trace_files_with_protocol_summary(self) -> None:
        worker = ExecutorReviewerWorker(get_settings())
        calls: list[dict[str, Any]] = []

        def fake_post_messages(*, messages: list[dict], tools: list[dict], api_key: str) -> dict:
            calls.append({"messages": copy.deepcopy(messages), "tools": tools, "api_key": api_key})
            if len(calls) == 1:
                return {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool_bad",
                            "name": "submit_executor_review",
                            "input": {"verdict": "maybe", "summary": 123},
                        }
                    ]
                }
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_good",
                        "name": "submit_executor_review",
                        "input": {
                            "verdict": "pass",
                            "summary": "Valid final review.",
                            "issues": [],
                            "repair_hints": [],
                            "inspected_files": ["runs/run_trace/manifest.json"],
                        },
                    }
                ]
            }

        worker._post_messages = fake_post_messages  # type: ignore[method-assign]
        original_key = worker.settings.deepseek_api_key
        worker.settings.deepseek_api_key = SecretStr("test-key")
        try:
            with tempfile.TemporaryDirectory(prefix="reviewer-trace-") as tmpdir:
                root = Path(tmpdir)
                result = worker.review(
                    root=root,
                    packet=TaskPacket.model_validate(
                        {
                            "task_id": "run_trace",
                            "project_id": "test-project",
                            "card_id": "card_review",
                            "goal": "Review executor output.",
                            "worker_instructions": "Run analysis.",
                        }
                    ),
                    manifest=Manifest.model_validate({"run_id": "run_trace", "status": "success", "summary": "done"}),
                    deterministic_issues=[],
                )
                trace_jsonl = root / "runs" / "run_trace" / "reviewer_trace.jsonl"
                trace_json = root / "runs" / "run_trace" / "reviewer_trace.json"
                self.assertTrue(trace_jsonl.exists())
                self.assertTrue(trace_json.exists())
                lines = [line for line in trace_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
                payload = json.loads(trace_json.read_text(encoding="utf-8"))
        finally:
            worker.settings.deepseek_api_key = original_key

        self.assertEqual("pass", result["verdict"])
        self.assertEqual(2, len(lines))
        self.assertEqual(1, payload["summary"]["submit_schema_failures"])
        self.assertEqual("invalid_submit_executor_review_schema", payload["summary"]["last_protocol_error_code"])
        self.assertEqual("runs/run_trace/reviewer_trace.jsonl", result["reviewer"]["trace_path"])

    def test_first_valid_final_review_is_not_overwritten_by_later_submit(self) -> None:
        worker = ExecutorReviewerWorker(get_settings())

        def fake_post_messages(*, messages: list[dict], tools: list[dict], api_key: str) -> dict:
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_first",
                        "name": "submit_executor_review",
                        "input": {
                            "verdict": "pass",
                            "summary": "First valid verdict.",
                            "issues": [],
                            "repair_hints": [],
                            "inspected_files": ["runs/run_review/manifest.json"],
                        },
                    },
                    {
                        "type": "tool_use",
                        "id": "tool_second",
                        "name": "submit_executor_review",
                        "input": {
                            "verdict": "fail",
                            "summary": "Second verdict should not replace the first.",
                            "issues": [{"severity": "error", "code": "late_failure", "message": "late"}],
                            "repair_hints": [],
                            "inspected_files": ["runs/run_review/manifest.json"],
                        },
                    },
                ]
            }

        worker._post_messages = fake_post_messages  # type: ignore[method-assign]
        original_key = worker.settings.deepseek_api_key
        worker.settings.deepseek_api_key = SecretStr("test-key")
        try:
            result = worker.review(
                root=Path(self._testMethodName),
                packet=TaskPacket.model_validate(
                    {
                        "task_id": "run_review",
                        "project_id": "test-project",
                        "card_id": "card_review",
                        "goal": "Review executor output.",
                        "worker_instructions": "Run analysis.",
                    }
                ),
                manifest=Manifest.model_validate({"run_id": "run_review", "status": "success", "summary": "done"}),
                deterministic_issues=[],
            )
        finally:
            worker.settings.deepseek_api_key = original_key

        self.assertEqual("pass", result["verdict"])
        self.assertEqual("First valid verdict.", result["summary"])

    def test_read_review_file_rejects_binary_content(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reviewer-binary-") as tmpdir:
            root = Path(tmpdir)
            run_id = "run_binary"
            binary = root / "results" / "plot.png"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
            packet = TaskPacket.model_validate(
                {
                    "task_id": run_id,
                    "project_id": "test-project",
                    "card_id": "card_review",
                    "goal": "Review executor output.",
                    "expected_outputs": [expected_output_spec("plot", "results/plot.png", artifact_class="figure")],
                    "allowed_paths": ["results/"],
                    "worker_instructions": "Run analysis.",
                }
            )
            manifest = Manifest.model_validate(
                {
                    "run_id": run_id,
                    "status": "success",
                    "summary": "done",
                    "created_assets": [{"role": "plot", "path": "results/plot.png"}],
                }
            )
            worker = ExecutorReviewerWorker(get_settings())

            result = worker._read_review_file(root, packet, manifest, "results/plot.png")

        self.assertFalse(result["ok"])
        self.assertIn("binary", result["error"])


if __name__ == "__main__":
    unittest.main()
