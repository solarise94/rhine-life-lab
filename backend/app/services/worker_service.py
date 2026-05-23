from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
from threading import Lock, Semaphore, Thread
from typing import Any
from uuid import uuid4
import re
import subprocess
import traceback

from fastapi import HTTPException

from app.models.cards import Card, CardAssetRef
from app.models.executor import (
    ExecutorContext,
    ExecutorReference,
    ExecutorStructuredEvent,
    ExecutorToolPolicy,
    ManagerReportingContract,
    RuntimeBindings,
)
from app.models.graph import Asset, Claim, Module, ReportItem, RunRecord
from app.models.runs import (
    ExpectedOutput,
    RunContext,
    RunEvent,
    TaskPacket,
    TaskPacketAsset,
    TaskPacketCardInput,
    TaskPacketCardOutput,
)
from app.services.flow_service import FlowService
from app.services.executor_validation_service import ExecutorValidationService
from app.services.manifest_service import ManifestService
from app.services.module_group_state_service import ModuleGroupStateService
from app.services.project_service import ProjectService
from app.services.runtime_approval_service import RuntimeApprovalService
from app.services.utils import atomic_write_json, utc_now
from app.workers import build_worker_registry


logger = logging.getLogger(__name__)


_SENSITIVE_COMMAND_KEYS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def _is_sensitive_command_key(value: str) -> bool:
    upper = value.upper()
    return any(marker in upper for marker in _SENSITIVE_COMMAND_KEYS)


def _redact_command_for_log(command: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next_setenv_value = False
    redact_next_option_value = False
    previous_token = ""
    for token in command:
        if redact_next_setenv_value:
            redacted.append("[REDACTED]")
            redact_next_setenv_value = False
            previous_token = token
            continue
        if redact_next_option_value:
            redacted.append("[REDACTED]")
            redact_next_option_value = False
            previous_token = token
            continue

        if previous_token == "--setenv":
            redacted.append(token)
            redact_next_setenv_value = _is_sensitive_command_key(token)
            previous_token = token
            continue

        if token in {"--api-key", "--token", "--password"}:
            redacted.append(token)
            redact_next_option_value = True
            previous_token = token
            continue

        if token.startswith("--api-key=") or token.startswith("--token=") or token.startswith("--password="):
            option, _value = token.split("=", 1)
            redacted.append(f"{option}=[REDACTED]")
            previous_token = token
            continue

        if "=" in token:
            key, _value = token.split("=", 1)
            if _is_sensitive_command_key(key):
                redacted.append(f"{key}=[REDACTED]")
                previous_token = token
                continue

        redacted.append(token)
        previous_token = token
    return redacted


class _CompositeExecutionGuard:
    def __init__(self, card_lock: Lock, semaphore: Semaphore) -> None:
        self.card_lock = card_lock
        self.semaphore = semaphore

    def release(self) -> None:
        self.semaphore.release()
        self.card_lock.release()


class WorkerService:
    def __init__(
        self,
        project_service: ProjectService,
        manifest_service: ManifestService,
        runtime_approval_service: RuntimeApprovalService,
    ) -> None:
        self.project_service = project_service
        self.manifest_service = manifest_service
        self.runtime_approval_service = runtime_approval_service
        self.flow_service = FlowService(project_service)
        self.executor_validation_service = ExecutorValidationService(project_service)
        self.registry = build_worker_registry()
        self._threads: dict[str, Thread] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._execution_locks: dict[str, Lock] = {}
        self._execution_semaphores: dict[str, Semaphore] = {}
        self._card_locks: dict[tuple[str, str], Lock] = {}
        self._execution_locks_guard = Lock()
        self._reconcile_active_runs()

    def start_run(
        self,
        project_id: str,
        card_id: str,
        worker_type: str | None = None,
        python_runtime: str | None = None,
        r_runtime: str | None = None,
    ) -> dict:
        python_runtime = self._normalize_python_runtime(python_runtime)
        r_runtime = self._normalize_r_runtime(r_runtime)
        resolved_worker_type = self._resolve_worker_type(worker_type)
        adapter = self.registry.get(resolved_worker_type)
        if adapter is None:
            raise HTTPException(status_code=400, detail=f"Unknown worker_type: {resolved_worker_type}")

        execution_guard, guard_kind = self._acquire_execution_guard(project_id, card_id, sandboxed=adapter.uses_sandbox(self.project_service.settings))
        if execution_guard is None:
            raise HTTPException(
                status_code=409,
                detail="Project already has an executor run in progress. Start another run after the current run finishes review.",
            )
        try:
            return self._start_run_with_execution_guard(
                project_id,
                card_id,
                resolved_worker_type,
                adapter,
                execution_guard,
                guard_kind,
                python_runtime=python_runtime,
                r_runtime=r_runtime,
            )
        except Exception:
            self._release_execution_guard(execution_guard, guard_kind)
            raise

    def _start_run_with_execution_guard(
        self,
        project_id: str,
        card_id: str,
        resolved_worker_type: str,
        adapter: Any,
        execution_guard: Lock | Semaphore,
        guard_kind: str,
        python_runtime: str | None = None,
        r_runtime: str | None = None,
    ) -> dict:
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            graph = store.load_graph()
            card = next((item for item in cards if item.card_id == card_id), None)
            if card is None:
                raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")
            work_item = self._get_work_item(project_id, card_id)
            if not work_item["can_start"]:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": f"Card {card_id} cannot start.",
                        "block_details": work_item,
                    },
                )

            run_id = self._new_run_id(graph.runs)
            run_dir = self.project_service.project_path(project_id) / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            packet = self._task_packet(
                project_id,
                run_id,
                card,
                graph.assets,
                resolved_worker_type,
                python_runtime=python_runtime,
                r_runtime=r_runtime,
            )
            atomic_write_json(run_dir / "task_packet.json", packet.model_dump())
            try:
                launch_spec = adapter.build_launch_spec(
                    packet=packet,
                    packet_path=run_dir / "task_packet.json",
                    run_dir=run_dir,
                    project_root=self.project_service.project_path(project_id),
                    settings=self.project_service.settings,
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

            approvals = [
                self.runtime_approval_service.review_permission_request(
                    project_id,
                    run_id,
                    {
                        "request_id": request.request_id,
                        "target": request.target,
                        "action": request.action,
                    },
                    readonly_paths=packet.readonly_paths,
                )
                for request in launch_spec.permission_requests
            ]
            unresolved = [item for item in approvals if item["decision"] == "needs_user_confirmation"]
            rejected = [item for item in approvals if item["decision"] in {"rejected", "user_rejected"}]
            (run_dir / "transcript.md").write_text(f"# {run_id}\n\nRun created.\n", encoding="utf-8")
            redacted_command = _redact_command_for_log(launch_spec.command)
            (run_dir / "commands.log").write_text(" ".join(redacted_command) + "\n", encoding="utf-8")

            initial_status = "needs_approval" if unresolved else "cancelled" if rejected else "queued"
            summary = "等待用户确认运行期权限请求。" if unresolved else "启动前权限校验失败。" if rejected else "等待执行器启动。"
            graph.runs.append(
                RunRecord(
                    run_id=run_id,
                    card_id=card_id,
                    module_id=card.linked_modules[0] if card.linked_modules else None,
                    status=initial_status,
                    title=f"{card.title} 执行",
                    summary=summary,
                    started_at=utc_now(),
                    finished_at=utc_now() if rejected else None,
                    worker_type=resolved_worker_type,
                )
            )
            card.status = "planned" if rejected else "running"
            card.progress_note = "执行器已创建，等待运行。" if not rejected else "启动前权限校验失败。"
            if rejected:
                card.manager_review = "运行期权限请求被拒绝，未启动执行器。"
            if run_id not in card.linked_runs:
                card.linked_runs.append(run_id)
            ModuleGroupStateService.sync_linked_module_status_from_card(card, graph.modules)
            ModuleGroupStateService.sync_group_hierarchy(cards, graph.modules)
            store.save_graph(graph)
            store.save_cards(cards)
            latest_event = RunEvent(
                event_id=f"evt_{run_id}_001",
                run_id=run_id,
                card_id=card_id,
                source="manager",
                event_type="run_created",
                visibility="bubble",
                preview_id=f"bubble_{card_id}",
                utterance_id=f"utt_{run_id}_001",
                stream_state="complete",
                message=f"已创建 run {run_id}，worker={resolved_worker_type}。",
                created_at=utc_now(),
            )
            store.save_run_events(run_id, [latest_event])
            for index, decision in enumerate(approvals, start=2):
                self._append_event(
                    project_id,
                    run_id,
                    card_id,
                    event_type="permission_decision",
                    message=f"[{decision['risk_level']}] {decision['target']} -> {decision['decision']}: {decision['reason']}",
                    sequence_hint=index,
                )

        response = {
            "run_id": run_id,
            "card_id": card_id,
            "worker_type": resolved_worker_type,
            "status": initial_status,
            "latest_event": latest_event.model_dump(),
        }
        if unresolved:
            response["pending_approvals"] = unresolved
            self._release_execution_guard(execution_guard, guard_kind)
            return response
        if rejected:
            response["rejected_approvals"] = rejected
            self._release_execution_guard(execution_guard, guard_kind)
            return response

        thread = Thread(
            target=self._execute_run_guarded,
            kwargs={
                "project_id": project_id,
                "run_id": run_id,
                "card_id": card_id,
                "worker_type": resolved_worker_type,
                "command": launch_spec.command,
                "cwd": launch_spec.cwd,
                "environment": launch_spec.environment,
                "execution_guard": execution_guard,
                "guard_kind": guard_kind,
                "sandboxed": launch_spec.sandboxed,
            },
            daemon=True,
        )
        self._threads[run_id] = thread
        thread.start()
        return response

    def continue_run_after_approval(self, project_id: str, run_id: str) -> dict:
        unresolved = self.runtime_approval_service.unresolved_user_requests(project_id, run_id)
        if unresolved:
            raise ValueError("Run still has unresolved approval requests.")
        decisions = self.runtime_approval_service.load_decisions(project_id, run_id)
        rejected = [item for item in decisions if item["decision"] in {"rejected", "user_rejected"}]
        store = self.project_service.graph_store(project_id)
        graph = store.load_graph()
        run = next(item for item in graph.runs if item.run_id == run_id)
        if rejected:
            self._append_event(
                project_id,
                run_id,
                run.card_id,
                event_type="run_cancelled",
                message="运行期权限请求被拒绝，run 已取消。",
            )
            self._set_run_status(
                project_id,
                run_id,
                run.card_id,
                status="cancelled",
                summary="运行期权限请求被拒绝。",
                progress_note=None,
                card_status="planned",
            )
            self._commit_run_stage(project_id, run_id, "cancelled")
            return {"run_id": run_id, "status": "cancelled", "rejected_approvals": rejected}
        if run.status != "needs_approval":
            return {"run_id": run_id, "status": run.status}

        adapter = self.registry.get(run.worker_type)
        if adapter is None:
            raise HTTPException(status_code=400, detail=f"Unknown worker_type: {run.worker_type}")
        execution_guard, guard_kind = self._acquire_execution_guard(project_id, run.card_id, sandboxed=adapter.uses_sandbox(self.project_service.settings))
        if execution_guard is None:
            raise HTTPException(
                status_code=409,
                detail="Project already has an executor run in progress. Continue this run after the current run finishes review.",
            )
        lock_transferred_to_thread = False
        try:
            packet = self.manifest_service.load_task_packet(project_id, run_id)
            run_dir = self.project_service.project_path(project_id) / "runs" / run_id
            try:
                launch_spec = adapter.build_launch_spec(
                    packet=packet,
                    packet_path=run_dir / "task_packet.json",
                    run_dir=run_dir,
                    project_root=self.project_service.project_path(project_id),
                    settings=self.project_service.settings,
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

            thread = Thread(
                target=self._execute_run_guarded,
                kwargs={
                    "project_id": project_id,
                    "run_id": run_id,
                    "card_id": run.card_id,
                    "worker_type": run.worker_type,
                    "command": launch_spec.command,
                    "cwd": launch_spec.cwd,
                    "environment": launch_spec.environment,
                    "execution_guard": execution_guard,
                    "guard_kind": guard_kind,
                    "sandboxed": launch_spec.sandboxed,
                },
                daemon=True,
            )
            self._threads[run_id] = thread
            thread.start()
            lock_transferred_to_thread = True
            return {"run_id": run_id, "status": "queued"}
        finally:
            if not lock_transferred_to_thread:
                self._release_execution_guard(execution_guard, guard_kind)

    def cancel_run(self, project_id: str, run_id: str, reason: str | None = None) -> dict:
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            graph = store.load_graph()
            run = next((item for item in graph.runs if item.run_id == run_id), None)
            if run is None:
                raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
            card = next(item for item in cards if item.card_id == run.card_id)
            if run.status == "cancelled":
                return {"run_id": run_id, "status": run.status, "summary": run.summary}
            if run.status not in {"queued", "needs_approval", "running", "reviewing"}:
                raise HTTPException(status_code=409, detail=f"Run {run_id} cannot be cancelled from status {run.status}.")
            message = (reason or "Run cancelled by operator.").strip()
            run.status = "cancelled"
            run.summary = message
            run.cancel_reason = message
            run.finished_at = utc_now()
            card.progress_note = None
            if not self._has_other_active_runs(graph.runs, card.card_id, exclude_run_id=run_id):
                card.status = "planned"
            card.manager_review = message
            ModuleGroupStateService.sync_linked_module_status_from_card(card, graph.modules)
            ModuleGroupStateService.sync_group_hierarchy(cards, graph.modules)
            store.save_graph(graph)
            store.save_cards(cards)

        process = self._processes.get(run_id)
        if process is not None and process.poll() is None:
            process.kill()
        self._append_event(project_id, run_id, run.card_id, event_type="run_cancelled", message=message)
        self._commit_run_stage(project_id, run_id, "cancelled")
        return {"run_id": run_id, "status": "cancelled", "summary": message}

    def cleanup_run(self, project_id: str, run_id: str, reason: str | None = None) -> dict:
        project_root = self.project_service.project_path(project_id)
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            graph = store.load_graph()
            run = next((item for item in graph.runs if item.run_id == run_id), None)
            if run is None:
                raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
            if run.cleanup_status == "completed":
                return {"run_id": run_id, "cleanup_status": run.cleanup_status, "archived_at": run.archived_at}
            if run.status not in {"success", "failed", "cancelled", "reviewed"}:
                raise HTTPException(status_code=409, detail=f"Run {run_id} cannot be cleaned up from status {run.status}.")
            if self._threads.get(run_id) and self._threads[run_id].is_alive():
                raise HTTPException(status_code=409, detail=f"Run {run_id} still has a live executor thread.")

            valid_assets = [asset.asset_id for asset in graph.assets if asset.created_by_run == run_id and asset.status == "valid"]
            if valid_assets:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": f"Run {run_id} has materialized valid assets and cannot be cleaned up.",
                        "asset_ids": valid_assets,
                    },
                )

            removed_asset_ids = {asset.asset_id for asset in graph.assets if asset.created_by_run == run_id}
            removed_claim_ids = {claim.claim_id for claim in graph.claims if claim.created_by_run == run_id}
            graph.assets = [asset for asset in graph.assets if asset.created_by_run != run_id]
            graph.claims = [claim for claim in graph.claims if claim.created_by_run != run_id]
            graph.report_items = [
                item
                for item in graph.report_items
                if item.item_id != f"report_{run_id}"
                and not removed_asset_ids.intersection(item.linked_asset_ids)
                and not removed_claim_ids.intersection(item.linked_claim_ids)
            ]
            for card in cards:
                card.linked_assets = [asset_id for asset_id in card.linked_assets if asset_id not in removed_asset_ids]
                card.outputs = [item for item in card.outputs if item.asset_id not in removed_asset_ids]

            run.cleanup_status = "completed"
            run.archived_at = utc_now()
            suffix = f" Cleanup reason: {reason.strip()}" if reason and reason.strip() else ""
            run.summary = f"{run.summary} Artifacts cleaned up.{suffix}".strip()
            graph.metadata["last_cleanup"] = {
                "run_id": run_id,
                "card_id": run.card_id,
                "created_at": run.archived_at,
            }
            store.save_graph(graph)
            store.save_cards(cards)

        shutil.rmtree(project_root / "runs" / run_id, ignore_errors=True)
        shutil.rmtree(project_root / "results" / run.card_id / run_id, ignore_errors=True)
        self._threads.pop(run_id, None)
        self._processes.pop(run_id, None)
        self._commit_run_stage(project_id, run_id, "cleanup")
        return {"run_id": run_id, "cleanup_status": "completed", "archived_at": run.archived_at}

    def reset_card_run_state(self, project_id: str, card_id: str) -> dict:
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            graph = store.load_graph()
            card = next((item for item in cards if item.card_id == card_id), None)
            if card is None:
                raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")
            if card.status not in {"failed", "reviewing", "needs_review", "rejected", "cancelled"}:
                raise HTTPException(status_code=409, detail=f"Card {card_id} cannot reset from status {card.status}.")
            if self._has_active_run(graph.runs, card_id):
                raise HTTPException(status_code=409, detail=f"Card {card_id} still has an active run.")
            card.status = "planned"
            card.progress_note = None
            card.manager_review = "Card run state reset to planned."
            ModuleGroupStateService.sync_linked_module_status_from_card(card, graph.modules)
            ModuleGroupStateService.sync_group_hierarchy(cards, graph.modules)
            store.save_graph(graph)
            store.save_cards(cards)
        return {"card_id": card_id, "status": "planned"}

    def rerun_card(
        self,
        project_id: str,
        card_id: str,
        worker_type: str | None = None,
        python_runtime: str | None = None,
        r_runtime: str | None = None,
    ) -> dict:
        python_runtime = self._normalize_python_runtime(python_runtime)
        r_runtime = self._normalize_r_runtime(r_runtime)
        old_execution_run_ids: list[str] = []
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            cards = store.load_cards()
            card = next((item for item in cards if item.card_id == card_id), None)
            if card is None:
                raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")
            if card.status in {"running", "reviewing", "proposed", "superseded", "stale"}:
                raise HTTPException(status_code=409, detail=f"Card {card_id} cannot rerun from status {card.status}.")
            if self._has_active_run(graph.runs, card_id):
                raise HTTPException(status_code=409, detail=f"Card {card_id} already has an active run.")
            old_execution_run_ids = [
                run.run_id
                for run in graph.runs
                if run.card_id == card_id
                and run.status in {"success", "failed", "cancelled", "reviewed"}
                and not (self._threads.get(run.run_id) and self._threads[run.run_id].is_alive())
            ]
            if card.status != "planned":
                previous_status = card.status
                card.status = "planned"
                card.progress_note = None
                card.manager_review = f"Preparing rerun from previous status {previous_status}."
                ModuleGroupStateService.sync_linked_module_status_from_card(card, graph.modules)
                ModuleGroupStateService.sync_group_hierarchy(cards, graph.modules)
                store.save_graph(graph)
                store.save_cards(cards)
        self._cleanup_execution_files_for_runs(project_id, old_execution_run_ids)
        return self.start_run(project_id, card_id, worker_type=worker_type, python_runtime=python_runtime, r_runtime=r_runtime)

    def review_run(self, project_id: str, run_id: str, accept: bool = True) -> dict:
        valid, errors = self.manifest_service.validate_manifest(project_id, run_id)
        if accept and not valid:
            raise ValueError("; ".join(errors))
        result = self._finalize_run_review(project_id, run_id, accept=accept, source="manager")
        self._append_event(
            project_id,
            run_id,
            result["card_id"],
            event_type="manager_review",
            message="Manager 已接受运行结果。" if accept else "Manager 已拒绝运行结果，保留 candidate 产物。",
        )
        self._commit_run_stage(project_id, run_id, "reviewed")
        return {"run_id": run_id, "accepted": accept}

    def _cleanup_execution_files_for_runs(self, project_id: str, run_ids: list[str]) -> None:
        if not run_ids:
            return
        project_root = self.project_service.project_path(project_id)
        for run_id in dict.fromkeys(run_ids):
            shutil.rmtree(project_root / "runs" / run_id, ignore_errors=True)
            shutil.rmtree(project_root / "scripts" / "generated" / run_id, ignore_errors=True)

    def _finalize_run_review(self, project_id: str, run_id: str, *, accept: bool, source: str) -> dict:
        review_context = self.manifest_service.manifest_to_review_context(project_id, run_id)
        task_packet = self.manifest_service.load_task_packet(project_id, run_id)
        manifest = self.manifest_service.load_manifest(project_id, run_id)
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            graph = store.load_graph()
            run = next(item for item in graph.runs if item.run_id == run_id)
            card = next(item for item in cards if item.card_id == run.card_id)
            previous_outputs_by_role = self._current_output_assets_by_role(card, graph.assets, current_run_id=run_id)
            previous_output_asset_ids = {asset.asset_id for asset in previous_outputs_by_role.values()}
            created_assets = self._materialize_run_assets(
                graph=graph,
                run_id=run_id,
                card=card,
                created_assets=review_context.created_assets,
                status="valid" if accept else "candidate",
                input_asset_ids=[item.asset_id for item in manifest.inputs_used],
            )
            if accept:
                new_claim_ids = self._materialize_claims(
                    graph, run_id, review_context.key_findings, [asset.asset_id for asset in created_assets]
                )
                card.status = "accepted"
                card.progress_note = None
                card.manager_review = "Reviewer 已验收执行器代码、manifest 和输出资产，结果已自动接受。" if source == "reviewer" else "结果已通过 manifest 校验并被 Manager 接受。"
                card.key_findings = review_context.key_findings or ["结果已生成并完成审核。"]
                self._attach_assets_to_card(card, created_assets)
                self._sync_card_outputs(
                    card,
                    created_assets,
                    manifest_created_assets=manifest.created_assets,
                    expected_outputs=task_packet.expected_outputs,
                )
                downstream_rebinds = self._rebind_downstream_inputs(
                    cards=cards,
                    modules=graph.modules,
                    assets=graph.assets,
                    claims=graph.claims,
                    producer_card=card,
                    previous_outputs_by_role=previous_outputs_by_role,
                    new_assets=created_assets,
                )
                self._supersede_previous_outputs(
                    card,
                    graph.assets,
                    graph.claims,
                    run_id,
                    previous_asset_ids=previous_output_asset_ids,
                )
                if downstream_rebinds:
                    graph.metadata["last_downstream_rebind"] = {
                        "run_id": run_id,
                        "card_id": card.card_id,
                        "rebinds": downstream_rebinds,
                        "created_at": utc_now(),
                    }
                graph.report_items = [item for item in graph.report_items if item.item_id != f"report_{run_id}"]
                graph.report_items.append(
                    ReportItem(
                        item_id=f"report_{run_id}",
                        section=card.title,
                        title=f"{card.title} 结果摘要",
                        summary=review_context.summary,
                        linked_asset_ids=[asset.asset_id for asset in created_assets if asset.report_selected],
                        linked_claim_ids=new_claim_ids,
                    )
                )
            else:
                card.status = "rejected"
                card.progress_note = None
                card.manager_review = "Reviewer 拒绝了这次运行结果，产出已保留为 candidate。" if source == "reviewer" else "Manager 拒绝了这次运行结果，产出已保留为 candidate。"
            ModuleGroupStateService.sync_linked_module_status_from_card(card, graph.modules)
            ModuleGroupStateService.sync_group_hierarchy(cards, graph.modules)
            run.status = "reviewed"
            brief = self._load_manager_brief(project_id, run_id)
            run.summary = brief.get("final_report", {}).get("summary") or review_context.summary
            run.finished_at = utc_now()
            store.save_graph(graph)
            store.save_cards(cards)
            return {"run_id": run_id, "card_id": card.card_id, "accepted": accept}

    def _execute_run(
        self,
        *,
        project_id: str,
        run_id: str,
        card_id: str,
        worker_type: str,
        command: list[str],
        cwd: Path,
        environment: dict[str, str],
        sandboxed: bool = False,
    ) -> None:
        if self._run_status(project_id, run_id) == "cancelled":
            self._threads.pop(run_id, None)
            return
        self._set_run_status(project_id, run_id, card_id, status="running", summary="执行器已启动。", progress_note="正在执行分析任务。")
        self._append_event(project_id, run_id, card_id, event_type="run_started", message=f"执行器 {worker_type} 已启动。")
        transcript_path = self.project_service.project_path(project_id) / "runs" / run_id / "transcript.md"

        try:
            before_snapshot = self.manifest_service.capture_filesystem_snapshot(project_id)
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self._processes[run_id] = process

            def pump_stdout() -> None:
                with transcript_path.open("a", encoding="utf-8") as transcript:
                    if process.stdout is None:
                        return
                    for raw_line in process.stdout:
                        line = raw_line.rstrip()
                        if not line:
                            continue
                        transcript.write(f"- {line}\n")
                        transcript.flush()
                        if self._handle_structured_executor_event(project_id, run_id, card_id, line):
                            continue
                        self._append_event(project_id, run_id, card_id, event_type="executor_output", message=line)

            reader = Thread(target=pump_stdout, daemon=True)
            reader.start()
            timed_out = False
            try:
                return_code = process.wait(timeout=self.project_service.settings.worker_timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                return_code = process.wait()
            reader.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()

            if self._run_status(project_id, run_id) == "cancelled":
                return

            audit_ok, audit_errors, _changes = self.manifest_service.audit_run_filesystem(
                project_id,
                run_id,
                before_snapshot,
                sandboxed=sandboxed,
            )
            dependency_issue_message, dependency_issue_payload = self._blocking_dependency_issue(project_id, run_id)
            if dependency_issue_message:
                self._append_event(
                    project_id,
                    run_id,
                    card_id,
                    event_type="runtime_dependency_missing",
                    message=dependency_issue_message,
                    payload=dependency_issue_payload,
                )
                self._set_run_attention(project_id, run_id, card_id, dependency_issue_message)
            if timed_out:
                message = dependency_issue_message or "执行超时，已终止。"
                if audit_errors:
                    message = f"{message} {'; '.join(audit_errors)}"
                self._append_event(project_id, run_id, card_id, event_type="run_failed", message=message)
                self._set_run_status(project_id, run_id, card_id, status="failed", summary=message, progress_note=None)
                if dependency_issue_message:
                    self._set_run_attention(project_id, run_id, card_id, dependency_issue_message)
                self._commit_run_stage(project_id, run_id, "failed")
                return

            if return_code != 0:
                message = dependency_issue_message or f"执行器退出码 {return_code}。"
                if audit_errors:
                    message = f"{message} {'; '.join(audit_errors)}"
                transcript_tail = self._transcript_tail(transcript_path)
                if transcript_tail and not dependency_issue_message:
                    message = f"{message} 最近输出：{transcript_tail}"
                self._append_event(project_id, run_id, card_id, event_type="run_failed", message=message)
                self._set_run_status(project_id, run_id, card_id, status="failed", summary=message, progress_note=None)
                if dependency_issue_message:
                    self._set_run_attention(project_id, run_id, card_id, dependency_issue_message)
                self._commit_run_stage(project_id, run_id, "failed")
                return

            if not audit_ok:
                message = "执行后文件系统审计失败：" + "; ".join(audit_errors)
                self._append_event(project_id, run_id, card_id, event_type="run_failed", message=message)
                self._set_run_status(project_id, run_id, card_id, status="failed", summary=message, progress_note=None)
                self._commit_run_stage(project_id, run_id, "failed")
                return

            if dependency_issue_message:
                self._append_event(project_id, run_id, card_id, event_type="run_failed", message=dependency_issue_message)
                self._set_run_status(project_id, run_id, card_id, status="failed", summary=dependency_issue_message, progress_note=None)
                self._set_run_attention(project_id, run_id, card_id, dependency_issue_message)
                self._commit_run_stage(project_id, run_id, "failed")
                return

            valid, errors = self.manifest_service.validate_manifest(project_id, run_id)
            if not valid:
                message = "Manifest 校验失败：" + "; ".join(errors)
                self._append_event(project_id, run_id, card_id, event_type="run_failed", message=message)
                self._set_run_status(project_id, run_id, card_id, status="failed", summary=message, progress_note=None)
                self._commit_run_stage(project_id, run_id, "failed")
                return

            manifest = self.manifest_service.load_manifest(project_id, run_id)
            self._append_event(project_id, run_id, card_id, event_type="review_started", message="Reviewer 正在验收执行器代码、manifest 和输出资产。")
            self._set_run_status(
                project_id,
                run_id,
                card_id,
                status="reviewing",
                summary="Reviewer 正在验收执行器结果。",
                progress_note="Reviewer 正在验收结果。",
                card_status="reviewing",
            )
            validation_report = self.executor_validation_service.validate_run(project_id, run_id)
            if self._run_status(project_id, run_id) == "cancelled":
                return
            if validation_report.status == "fail":
                message = "Executor validation failed: " + validation_report.summary
                self._append_event(
                    project_id,
                    run_id,
                    card_id,
                    event_type="executor_issue",
                    message=message,
                    payload=validation_report.model_dump(),
                )
                self._set_run_status(project_id, run_id, card_id, status="failed", summary=message, progress_note=None)
                self._commit_run_stage(project_id, run_id, "failed")
                return
            validation_warning_message = None
            if validation_report.status == "warn":
                warning_codes = ", ".join(sorted({issue.code for issue in validation_report.issues})) or "validation_warning"
                validation_warning_message = f"Executor validation warning ({warning_codes}): {validation_report.summary}"
            self._append_event(
                project_id,
                run_id,
                card_id,
                event_type="executor_validation",
                message=validation_report.summary,
                payload=validation_report.model_dump(),
            )
            manager_brief = self._load_manager_brief(project_id, run_id)
            summary = manager_brief.get("final_report", {}).get("summary") or manifest.summary
            self._finalize_run_review(project_id, run_id, accept=True, source="reviewer")
            if validation_warning_message:
                self._set_run_attention(project_id, run_id, card_id, validation_warning_message)
                self._append_event(
                    project_id,
                    run_id,
                    card_id,
                    event_type="executor_validation_warning",
                    message=validation_warning_message,
                    payload=validation_report.model_dump(),
                )
            self._append_event(
                project_id,
                run_id,
                card_id,
                event_type="reviewer_acceptance",
                message=f"Reviewer 已验收并接受运行结果。{summary}",
                payload=validation_report.model_dump(),
            )
            self._commit_run_stage(project_id, run_id, "reviewed")
        except Exception as exc:
            message = f"执行器运行后处理失败：{exc}"
            logger.exception("Post-run handling failed for project=%s run=%s", project_id, run_id)
            try:
                self._append_event(project_id, run_id, card_id, event_type="run_failed", message=message)
                self._set_run_status(project_id, run_id, card_id, status="failed", summary=message, progress_note=None)
                self._commit_run_stage(project_id, run_id, "failed")
            except Exception as secondary_exc:
                logger.exception("Failed to persist post-run failure state for project=%s run=%s", project_id, run_id)
                self._write_run_fatal_error(
                    project_id,
                    run_id,
                    "Failed to persist post-run failure state.",
                    primary_exception=exc,
                    secondary_exception=secondary_exc,
                )
        finally:
            self._processes.pop(run_id, None)
            self._threads.pop(run_id, None)

    def _execute_run_guarded(
        self,
        *,
        project_id: str,
        run_id: str,
        card_id: str,
        worker_type: str,
        command: list[str],
        cwd: Path,
        environment: dict[str, str],
        execution_guard: Lock | Semaphore,
        guard_kind: str,
        sandboxed: bool = False,
    ) -> None:
        try:
            self._execute_run(
                project_id=project_id,
                run_id=run_id,
                card_id=card_id,
                worker_type=worker_type,
                command=command,
                cwd=cwd,
                environment=environment,
                sandboxed=sandboxed,
            )
        finally:
            self._release_execution_guard(execution_guard, guard_kind)

    def _acquire_execution_guard(self, project_id: str, card_id: str, *, sandboxed: bool) -> tuple[Lock | Semaphore | None, str]:
        if not sandboxed:
            execution_lock = self._execution_lock_for(project_id)
            if not execution_lock.acquire(blocking=False):
                return None, "lock"
            return execution_lock, "lock"
        card_lock = self._card_lock_for(project_id, card_id)
        if not card_lock.acquire(blocking=False):
            return None, "card_lock"
        semaphore = self._execution_semaphore_for(project_id)
        if not semaphore.acquire(blocking=False):
            card_lock.release()
            return None, "semaphore"
        return _CompositeExecutionGuard(card_lock, semaphore), "composite"

    @staticmethod
    def _release_execution_guard(execution_guard: Lock | Semaphore, guard_kind: str) -> None:
        if guard_kind == "composite" and isinstance(execution_guard, _CompositeExecutionGuard):
            execution_guard.release()
            return
        execution_guard.release()

    def _execution_lock_for(self, project_id: str) -> Lock:
        with self._execution_locks_guard:
            if project_id not in self._execution_locks:
                self._execution_locks[project_id] = Lock()
            return self._execution_locks[project_id]

    def _execution_semaphore_for(self, project_id: str) -> Semaphore:
        with self._execution_locks_guard:
            if project_id not in self._execution_semaphores:
                self._execution_semaphores[project_id] = Semaphore(max(1, int(self.project_service.settings.executor_max_concurrent_runs)))
            return self._execution_semaphores[project_id]

    def _card_lock_for(self, project_id: str, card_id: str) -> Lock:
        key = (project_id, card_id)
        with self._execution_locks_guard:
            if key not in self._card_locks:
                self._card_locks[key] = Lock()
            return self._card_locks[key]

    @staticmethod
    def _transcript_tail(transcript_path: Path, *, max_lines: int = 6, max_chars: int = 800) -> str:
        if not transcript_path.exists():
            return ""
        lines = [
            line.removeprefix("- ").strip()
            for line in transcript_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        tail = " | ".join(lines[-max_lines:])
        if len(tail) > max_chars:
            return tail[-max_chars:]
        return tail

    def _set_run_status(
        self,
        project_id: str,
        run_id: str,
        card_id: str,
        *,
        status: str,
        summary: str,
        progress_note: str | None,
        card_status: str | None = None,
    ) -> None:
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            graph = store.load_graph()
            run = next((item for item in graph.runs if item.run_id == run_id), None)
            if run is None:
                return
            card = next(item for item in cards if item.card_id == card_id)
            run.status = status
            run.summary = summary
            if status in {"success", "failed", "cancelled", "reviewed"}:
                run.finished_at = utc_now()
            if card_status:
                card.status = card_status
            elif status == "failed":
                card.status = "failed"
                card.manager_review = summary
            card.progress_note = progress_note
            ModuleGroupStateService.sync_linked_module_status_from_card(card, graph.modules)
            ModuleGroupStateService.sync_group_hierarchy(cards, graph.modules)
            store.save_graph(graph)
            store.save_cards(cards)

    def _append_event(
        self,
        project_id: str,
        run_id: str,
        card_id: str,
        *,
        event_type: str,
        message: str,
        sequence_hint: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            events = store.load_run_events(run_id)
            sequence = sequence_hint or (len(events) + 1)
            events.append(
                RunEvent(
                    event_id=f"evt_{run_id}_{sequence:03d}",
                    run_id=run_id,
                    card_id=card_id,
                    source="executor"
                    if event_type.startswith("run_") or event_type.startswith("executor_")
                    else "manager",
                    event_type=event_type,
                    visibility="bubble",
                    preview_id=f"bubble_{card_id}",
                    utterance_id=f"utt_{run_id}_{sequence:03d}",
                    stream_state="complete",
                    message=message,
                    created_at=utc_now(),
                    payload=payload or {},
                )
            )
            store.save_run_events(run_id, events)

    def _write_run_fatal_error(
        self,
        project_id: str,
        run_id: str,
        message: str,
        *,
        primary_exception: Exception | None = None,
        secondary_exception: Exception | None = None,
    ) -> None:
        try:
            run_dir = self.project_service.project_path(project_id) / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            lines = [message, f"created_at: {utc_now()}"]
            if primary_exception is not None:
                lines.extend(
                    [
                        "",
                        "primary_exception:",
                        "".join(traceback.format_exception(primary_exception)).rstrip(),
                    ]
                )
            if secondary_exception is not None:
                lines.extend(
                    [
                        "",
                        "secondary_exception:",
                        "".join(traceback.format_exception(secondary_exception)).rstrip(),
                    ]
                )
            (run_dir / "fatal_error.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            logger.exception("Failed to write fatal run error log for project=%s run=%s", project_id, run_id)

    def _handle_structured_executor_event(self, project_id: str, run_id: str, card_id: str, line: str) -> bool:
        contract = self._build_manager_reporting_contract()
        if not line.startswith(contract.stdout_prefix):
            return False
        payload_text = line[len(contract.stdout_prefix) :].strip()
        try:
            event = ExecutorStructuredEvent.model_validate_json(payload_text)
        except Exception as exc:
            self._append_event(
                project_id,
                run_id,
                card_id,
                event_type="executor_output",
                message=f"Invalid BP_EVENT payload: {exc}",
            )
            return True

        if event.type == "progress_update":
            message = event.message or f"Executor progress update: {event.stage or 'unknown'}"
            self._append_event(
                project_id,
                run_id,
                card_id,
                event_type="executor_progress",
                message=message,
                payload=event.model_dump(),
            )
            if event.message:
                self._set_run_progress_note(project_id, run_id, card_id, event.message)
        elif event.type == "issue_report":
            message = event.message or "Executor reported an issue."
            self._append_event(
                project_id,
                run_id,
                card_id,
                event_type="executor_issue",
                message=message,
                payload=event.model_dump(),
            )
            if event.needs_manager:
                self._set_run_attention(project_id, run_id, card_id, message)
                self._append_event(
                    project_id,
                    run_id,
                    card_id,
                    event_type="run_blocked_on_manager",
                    message=message,
                    payload=event.model_dump(),
                )
        else:
            message = event.summary or event.message or "Executor final report received."
            self._append_event(
                project_id,
                run_id,
                card_id,
                event_type="executor_final_report",
                message=message,
                payload=event.model_dump(),
            )
            if event.summary:
                self._set_run_summary(project_id, run_id, event.summary)

        self._save_manager_brief(project_id, run_id, event)
        return True

    def _task_packet(
        self,
        project_id: str,
        run_id: str,
        card: Card,
        assets: list[Asset],
        worker_type: str,
        python_runtime: str | None = None,
        r_runtime: str | None = None,
    ) -> TaskPacket:
        asset_map = {asset.asset_id: asset for asset in assets}
        input_asset_ids = list(dict.fromkeys([item.asset_id for item in card.inputs if item.asset_id]))
        input_assets = [
            TaskPacketAsset(
                asset_id=asset.asset_id,
                path=asset.path,
                type=asset.asset_type,
                title=asset.title,
                status=asset.status,
            )
            for asset_id in input_asset_ids
            for asset in [asset_map.get(asset_id)]
            if asset is not None
        ]
        card_inputs = [
            TaskPacketCardInput(
                label=item.label,
                asset_id=item.asset_id,
                asset_path=asset_map[item.asset_id].path if item.asset_id and item.asset_id in asset_map else None,
                asset_type=asset_map[item.asset_id].asset_type if item.asset_id and item.asset_id in asset_map else None,
                status=asset_map[item.asset_id].status if item.asset_id and item.asset_id in asset_map else "missing",
            )
            for item in card.inputs
        ]
        output_refs = [
            item
            for item in (card.outputs or [CardAssetRef(label="Run summary")])
            if not (item.asset_id and asset_map.get(item.asset_id) and asset_map[item.asset_id].created_by_run)
        ]
        if not output_refs:
            output_refs = [CardAssetRef(label="Run summary")]
        card_outputs: list[TaskPacketCardOutput] = []
        expected_outputs: list[ExpectedOutput] = []
        for index, item in enumerate(output_refs, start=1):
            role, output_type, path_hint = self._infer_output_spec(card.card_id, run_id, item, index)
            card_outputs.append(
                TaskPacketCardOutput(
                    label=item.label,
                    asset_id=item.asset_id,
                    status=item.status,
                    role=role,
                    type=output_type,
                    path_hint=path_hint,
                )
            )
            expected_outputs.append(
                ExpectedOutput(
                    role=role,
                    type=output_type,
                    path_hint=path_hint,
                    label=item.label,
                    asset_id=item.asset_id,
                )
            )

        existing_roles = {item.role for item in expected_outputs}
        if "run_summary" not in existing_roles:
            expected_outputs.append(
                ExpectedOutput(
                    role="run_summary",
                    type="markdown",
                    path_hint=f"results/{card.card_id}/{run_id}/run_summary.md",
                    label="Run summary",
                )
            )
        if "run_preview" not in existing_roles:
            expected_outputs.append(
                ExpectedOutput(
                    role="run_preview",
                    type="figure",
                    path_hint=f"results/{card.card_id}/{run_id}/run_preview.svg",
                    label="Run preview",
                )
            )

        result_dir = f"results/{card.card_id}/{run_id}"
        return TaskPacket(
            task_id=run_id,
            project_id=project_id,
            card_id=card.card_id,
            card_title=card.title,
            card_status=card.status,
            goal=card.summary,
            input_assets=input_assets,
            card_inputs=card_inputs,
            card_outputs=card_outputs,
            expected_outputs=expected_outputs,
            allowed_paths=[f"runs/{run_id}/", f"{result_dir}/", "scripts/generated/"],
            readonly_paths=[asset.path for asset in input_assets],
            forbidden_paths=[".git/", "graph/"],
            execution_policy={
                "mode": "guarded",
                "network": "prompt",
                "write_policy": "allowed_paths_with_post_run_audit",
                "on_policy_violation": "fail_or_quarantine",
            },
            constraints=[
                "Do not overwrite existing valid assets.",
                f"Write outputs under {result_dir}/",
                f"Write manifest to runs/{run_id}/manifest.json.",
                "Declare every consumed input in manifest.inputs_used.",
                "Do not modify graph/, .git/, or upstream input assets.",
                f"Do not install missing runtime packages. If required packages/tools are unavailable, use runs/{run_id}/report_dependency_issue.py to report them and stop.",
            ],
            worker_instructions=(
                "You are a bioinformatics worker agent. Read task_packet.json, use the declared inputs, "
                "write only inside allowed_paths, and produce a complete manifest that matches expected_outputs."
            ),
            run_context=RunContext(
                run_id=run_id,
                worker_type=worker_type,
                project_root=str(self.project_service.project_path(project_id)),
                run_dir=f"runs/{run_id}",
                result_dir=result_dir,
            ),
            executor_context=self._build_executor_context(project_id, card, worker_type, python_runtime=python_runtime, r_runtime=r_runtime),
            manager_reporting_contract=self._build_manager_reporting_contract(),
        )

    @staticmethod
    def _normalize_python_runtime(python_runtime: str | None) -> str | None:
        if python_runtime in {None, "", "__system__"}:
            return None
        return python_runtime

    @staticmethod
    def _normalize_r_runtime(r_runtime: str | None) -> str | None:
        if r_runtime in {None, "", "__system__"}:
            return None
        return r_runtime

    def _build_executor_context(
        self,
        project_id: str,
        card: Card,
        worker_type: str,
        python_runtime: str | None = None,
        r_runtime: str | None = None,
    ) -> ExecutorContext:
        graph = self.project_service.graph_store(project_id).load_graph()
        default_context = self._default_executor_context(
            graph,
            card,
            worker_type,
            python_runtime=python_runtime,
            r_runtime=r_runtime,
        )
        if card.executor_context is not None:
            context = self._merge_executor_context(default_context, card.executor_context)
            if python_runtime:
                context.runtime_bindings.conda_env = python_runtime
                context.runtime_bindings.env["BLUEPRINT_PYTHON_RUNTIME"] = python_runtime
            if r_runtime:
                context.runtime_bindings.r_env = r_runtime
                context.runtime_bindings.env["BLUEPRINT_R_RUNTIME"] = r_runtime
            return context
        return default_context

    @staticmethod
    def _default_executor_context(
        graph: Any,
        card: Card,
        worker_type: str,
        python_runtime: str | None = None,
        r_runtime: str | None = None,
    ) -> ExecutorContext:
        conda_env = python_runtime or graph.metadata.get("default_conda_env")
        r_env = r_runtime or graph.metadata.get("default_r_env")
        runtime_env = {}
        if python_runtime:
            runtime_env["BLUEPRINT_PYTHON_RUNTIME"] = python_runtime
        if r_runtime:
            runtime_env["BLUEPRINT_R_RUNTIME"] = r_runtime
        network_policy = "allow" if worker_type in {"opencode", "codex", "pi", "claude_code"} else "prompt"
        return ExecutorContext(
            executor_profile=f"{worker_type}_worker",
            skills=[module_id.replace("module_", "") for module_id in card.linked_modules],
            instruction_blocks=[
                "Prefer reproducible scripts over ad-hoc shell pipelines.",
                "Summarize findings conservatively and keep outputs traceable to inputs.",
            ],
            references=[
                ExecutorReference(type="file", path="configs/params.yaml", description="Project-level runtime parameters."),
            ],
            tool_policy=ExecutorToolPolicy(network=network_policy, python=True, rscript=worker_type in {"pi", "opencode"}),
            runtime_bindings=RuntimeBindings(
                conda_env=conda_env,
                r_env=r_env,
                working_dir=".",
                env=runtime_env,
            ),
        )

    @staticmethod
    def _merge_executor_context(default_context: ExecutorContext, override: ExecutorContext) -> ExecutorContext:
        context = default_context.model_copy(deep=True)
        if override.executor_profile is not None:
            context.executor_profile = override.executor_profile
        if "skills" in override.model_fields_set:
            context.skills = list(override.skills)
        if "instruction_blocks" in override.model_fields_set:
            context.instruction_blocks = list(override.instruction_blocks)
        if "references" in override.model_fields_set:
            context.references = [item.model_copy(deep=True) for item in override.references]
        if "tool_policy" in override.model_fields_set:
            policy_fields = override.tool_policy.model_fields_set
            if "network" in policy_fields and override.tool_policy.network in {"allow", "deny"}:
                context.tool_policy.network = override.tool_policy.network
            if "python" in policy_fields:
                context.tool_policy.python = override.tool_policy.python
            if "rscript" in policy_fields:
                context.tool_policy.rscript = override.tool_policy.rscript
            if "shell" in policy_fields:
                context.tool_policy.shell = override.tool_policy.shell
            if "git_write" in policy_fields:
                context.tool_policy.git_write = override.tool_policy.git_write
        if "runtime_bindings" in override.model_fields_set:
            runtime_fields = override.runtime_bindings.model_fields_set
            if "conda_env" in runtime_fields:
                context.runtime_bindings.conda_env = override.runtime_bindings.conda_env
            if "r_env" in runtime_fields:
                context.runtime_bindings.r_env = override.runtime_bindings.r_env
            if "container_image" in runtime_fields:
                context.runtime_bindings.container_image = override.runtime_bindings.container_image
            if "working_dir" in runtime_fields:
                context.runtime_bindings.working_dir = override.runtime_bindings.working_dir
            if "env" in runtime_fields:
                context.runtime_bindings.env.update(override.runtime_bindings.env)
        return context

    @staticmethod
    def _build_manager_reporting_contract() -> ManagerReportingContract:
        return ManagerReportingContract(
            transport="stdout_bp_event",
            stdout_prefix="BP_EVENT ",
            file_path="runs/{run_id}/manager_updates.jsonl",
        )

    def _save_manager_brief(self, project_id: str, run_id: str, event: ExecutorStructuredEvent) -> None:
        run_dir = self.project_service.project_path(project_id) / "runs" / run_id
        path = run_dir / "manager_brief.json"
        brief = self._load_manager_brief(project_id, run_id)
        brief["run_id"] = run_id
        if event.type == "progress_update":
            brief["latest_progress"] = event.model_dump()
        elif event.type == "issue_report":
            issues = list(brief.get("issues") or [])
            issues.append(event.model_dump())
            brief["issues"] = issues
        elif event.type == "final_report":
            brief["final_report"] = event.model_dump()
        atomic_write_json(path, brief)

    def _load_manager_brief(self, project_id: str, run_id: str) -> dict[str, Any]:
        path = self.project_service.project_path(project_id) / "runs" / run_id / "manager_brief.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _blocking_dependency_issue(self, project_id: str, run_id: str) -> tuple[str | None, dict[str, Any]]:
        issue_path = self.project_service.project_path(project_id) / "runs" / run_id / "dependency_issue.json"
        file_payload: dict[str, Any] = {}
        if issue_path.exists():
            try:
                file_payload = json.loads(issue_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                file_payload = {}
        brief = self._load_manager_brief(project_id, run_id)
        issues = list(file_payload.get("issues") or [])
        if not issues:
            issues = list(brief.get("dependency_issues") or [])
        if not issues:
            issues = [
                issue
                for issue in list(brief.get("issues") or [])
                if isinstance(issue, dict) and issue.get("metadata", {}).get("issue_kind") == "runtime_dependency_missing"
            ]
        blocking = [
            issue
            for issue in issues
            if isinstance(issue, dict) and issue.get("metadata", {}).get("blocking", True)
        ]
        if not blocking:
            return None, {"issues": issues}
        missing: list[str] = []
        ecosystems: list[str] = []
        for issue in blocking:
            metadata = issue.get("metadata") or {}
            ecosystem = metadata.get("ecosystem")
            if isinstance(ecosystem, str) and ecosystem and ecosystem not in ecosystems:
                ecosystems.append(ecosystem)
            for package in metadata.get("missing_packages") or []:
                if isinstance(package, str) and package and package not in missing:
                    missing.append(package)
        if missing:
            ecosystem_text = "/".join(ecosystems) if ecosystems else "runtime"
            message = f"执行器报告运行环境依赖不足：缺乏 {ecosystem_text} 包/工具 {', '.join(missing)}。"
        else:
            first_message = next((issue.get("message") for issue in blocking if isinstance(issue.get("message"), str)), None)
            message = first_message or "执行器报告运行环境依赖不足。"
        return message, {"issues": blocking}

    def _set_run_progress_note(self, project_id: str, run_id: str, card_id: str, progress_note: str) -> None:
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            graph = store.load_graph()
            run = next(item for item in graph.runs if item.run_id == run_id)
            card = next(item for item in cards if item.card_id == card_id)
            run.summary = progress_note
            card.progress_note = progress_note
            store.save_runs(graph.runs)
            store.save_cards(cards)

    def _set_run_summary(self, project_id: str, run_id: str, summary: str) -> None:
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            run = next(item for item in graph.runs if item.run_id == run_id)
            run.summary = summary
            store.save_runs(graph.runs)

    def _set_run_attention(self, project_id: str, run_id: str, card_id: str, message: str) -> None:
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            graph = store.load_graph()
            run = next(item for item in graph.runs if item.run_id == run_id)
            card = next(item for item in cards if item.card_id == card_id)
            run.needs_manager_attention = True
            card.progress_note = message
            store.save_runs(graph.runs)
            store.save_cards(cards)

    @staticmethod
    def _has_active_run(runs: list[RunRecord], card_id: str) -> bool:
        return any(run.card_id == card_id and run.status in {"queued", "needs_approval", "running", "reviewing"} for run in runs)

    @staticmethod
    def _has_other_active_runs(runs: list[RunRecord], card_id: str, exclude_run_id: str) -> bool:
        return any(
            run.card_id == card_id and run.run_id != exclude_run_id and run.status in {"queued", "needs_approval", "running", "reviewing"}
            for run in runs
        )

    def _run_status(self, project_id: str, run_id: str) -> str | None:
        graph = self.project_service.graph_store(project_id).load_graph()
        run = next((item for item in graph.runs if item.run_id == run_id), None)
        return run.status if run else None

    @staticmethod
    def _materialize_run_assets(
        *,
        graph: object,
        run_id: str,
        card: Card,
        created_assets: list[dict],
        status: str,
        input_asset_ids: list[str],
    ) -> list[Asset]:
        root_graph = graph
        assets: list[Asset] = []
        for index, item in enumerate(created_assets, start=1):
            asset_id = f"asset_{run_id}_{item['role']}_{index}"
            existing = next((asset for asset in root_graph.assets if asset.asset_id == asset_id), None)
            if existing:
                existing.status = status
                asset = existing
            else:
                asset = Asset(
                    asset_id=asset_id,
                    asset_type=item["type"],
                    title=f"{card.title} {item['role']}".strip(),
                    status=status,
                    created_by_run=run_id,
                    path=item["path"],
                    depends_on=input_asset_ids,
                    summary=item.get("description") or f"{card.title} 产出文件。",
                    metadata={
                        "role": item["role"],
                        "planned_asset_id": item.get("asset_id"),
                        "sha256": item.get("sha256"),
                        "size_bytes": item.get("size_bytes"),
                    },
                    report_selected=item["type"] == "markdown",
                )
                root_graph.assets.append(asset)
            assets.append(asset)
        return assets

    @staticmethod
    def _materialize_claims(graph: object, run_id: str, findings: list[str], asset_ids: list[str]) -> list[str]:
        claim_ids: list[str] = []
        for index, finding in enumerate(findings, start=1):
            claim_id = f"claim_{run_id}_{index:02d}"
            existing = next((claim for claim in graph.claims if claim.claim_id == claim_id), None)
            if existing:
                existing.text = finding
                existing.status = "valid"
            else:
                graph.claims.append(
                    Claim(
                        claim_id=claim_id,
                        text=finding,
                        status="valid",
                        depends_on_assets=asset_ids,
                        created_by_run=run_id,
                        report_selected=True,
                    )
                )
            claim_ids.append(claim_id)
        return claim_ids

    @staticmethod
    def _attach_assets_to_card(card: Card, assets: list[Asset]) -> None:
        for asset in assets:
            if asset.asset_id not in card.linked_assets:
                card.linked_assets.append(asset.asset_id)

    @staticmethod
    def _sync_card_outputs(
        card: Card,
        assets: list[Asset],
        *,
        manifest_created_assets: list[object] | None = None,
        expected_outputs: list[object] | None = None,
    ) -> None:
        expected_asset_id_by_role = {
            getattr(item, "role", ""): getattr(item, "asset_id", None)
            for item in (expected_outputs or [])
            if getattr(item, "asset_id", None)
        }
        real_by_planned: dict[str, Asset] = {}
        if manifest_created_assets is not None:
            for asset, manifest_asset in zip(assets, manifest_created_assets, strict=False):
                role = getattr(manifest_asset, "role", "")
                planned_asset_id = getattr(manifest_asset, "asset_id", None) or expected_asset_id_by_role.get(role)
                if planned_asset_id:
                    real_by_planned[planned_asset_id] = asset

        for output in card.outputs:
            if output.asset_id in real_by_planned:
                real_asset = real_by_planned[output.asset_id]
                output.asset_id = real_asset.asset_id
                output.status = real_asset.status

        output_map = {item.asset_id for item in card.outputs if item.asset_id}
        for asset in assets:
            if asset.asset_id not in output_map:
                card.outputs.append(CardAssetRef(label=asset.title, asset_id=asset.asset_id))

    @staticmethod
    def _current_output_assets_by_role(card: Card, assets: list[Asset], *, current_run_id: str) -> dict[str, Asset]:
        linked_asset_ids = set(card.linked_assets)
        input_asset_ids = {item.asset_id for item in card.inputs if item.asset_id}
        outputs_by_role: dict[str, Asset] = {}
        for asset in assets:
            if asset.asset_id not in linked_asset_ids:
                continue
            if asset.asset_id in input_asset_ids:
                continue
            if asset.created_by_run == current_run_id:
                continue
            role = str(asset.metadata.get("role") or asset.asset_id or "")
            if role and asset.status == "valid":
                outputs_by_role[role] = asset
        return outputs_by_role

    @staticmethod
    def _rebind_downstream_inputs(
        *,
        cards: list[Card],
        modules: list[Module],
        assets: list[Asset],
        claims: list[Claim],
        producer_card: Card,
        previous_outputs_by_role: dict[str, Asset],
        new_assets: list[Asset],
    ) -> list[dict[str, str]]:
        role_to_new_asset = {
            str(asset.metadata.get("role") or asset.metadata.get("planned_asset_id") or ""): asset
            for asset in new_assets
            if asset.status == "valid" and (asset.metadata.get("role") or asset.metadata.get("planned_asset_id"))
        }
        replacements = {
            old_asset.asset_id: role_to_new_asset[role].asset_id
            for role, old_asset in previous_outputs_by_role.items()
            if role in role_to_new_asset and old_asset.asset_id != role_to_new_asset[role].asset_id
        }
        if not replacements:
            return []

        rebinds: list[dict[str, str]] = []
        for card in cards:
            if card.card_id == producer_card.card_id:
                continue
            touched = False
            for item in card.inputs:
                if item.asset_id in replacements:
                    item.asset_id = replacements[item.asset_id]
                    item.status = "materialized"
                    touched = True
                    rebinds.append({"target": "card_input", "card_id": card.card_id, "asset_id": item.asset_id})
            linked_assets = [replacements.get(asset_id, asset_id) for asset_id in card.linked_assets]
            if linked_assets != card.linked_assets:
                card.linked_assets = list(dict.fromkeys(linked_assets))
                touched = True
                rebinds.append({"target": "card_linked_assets", "card_id": card.card_id, "asset_id": ",".join(card.linked_assets)})
            if touched and card.status in {"accepted", "rejected"}:
                card.status = "stale"
                card.progress_note = "上游输入资产已更新，需要重新审核或重跑。"

        for module in modules:
            next_depends = [replacements.get(asset_id, asset_id) for asset_id in module.depends_on_assets]
            if next_depends != module.depends_on_assets:
                module.depends_on_assets = list(dict.fromkeys(next_depends))
                if module.status in {"accepted", "rejected"}:
                    module.status = "stale"
                rebinds.append({"target": "module_depends_on_assets", "module_id": module.module_id, "asset_id": ",".join(module.depends_on_assets)})

        for asset in assets:
            next_depends = [replacements.get(asset_id, asset_id) for asset_id in asset.depends_on]
            if next_depends != asset.depends_on:
                asset.depends_on = list(dict.fromkeys(next_depends))
                if asset.status == "valid":
                    asset.status = "stale"
                rebinds.append({"target": "asset_depends_on", "asset_id": asset.asset_id, "depends_on": ",".join(asset.depends_on)})

        for claim in claims:
            next_depends = [replacements.get(asset_id, asset_id) for asset_id in claim.depends_on_assets]
            if next_depends != claim.depends_on_assets:
                claim.depends_on_assets = list(dict.fromkeys(next_depends))
                if claim.status == "valid":
                    claim.status = "stale"
                rebinds.append({"target": "claim_depends_on_assets", "claim_id": claim.claim_id, "asset_id": ",".join(claim.depends_on_assets)})
        return rebinds

    @staticmethod
    def _supersede_previous_outputs(
        card: Card,
        assets: list[Asset],
        claims: list[Claim],
        current_run_id: str,
        *,
        previous_asset_ids: set[str] | None = None,
    ) -> None:
        current_asset_ids = set(card.linked_assets) if previous_asset_ids is None else previous_asset_ids
        for asset in assets:
            if (
                asset.asset_id in current_asset_ids
                and asset.created_by_run
                and asset.created_by_run != current_run_id
                and asset.status == "valid"
            ):
                asset.status = "superseded"
        stale_assets = {asset.asset_id for asset in assets if asset.status == "superseded"}
        for claim in claims:
            if stale_assets.intersection(claim.depends_on_assets) and claim.status == "valid":
                claim.status = "superseded"

    def _resolve_worker_type(self, worker_type: str | None) -> str:
        if worker_type:
            return worker_type
        default_worker_type = self.project_service.settings.default_worker_type
        candidates = list(dict.fromkeys([default_worker_type, "pi", "opencode", "codex", "claude_code"]))
        for candidate in candidates:
            if self._is_worker_configured(candidate):
                return candidate
        raise HTTPException(
            status_code=409,
            detail=(
                "No configured executor is available. Configure BLUEPRINT_PI_COMMAND for the real pi CLI "
                "or set one of BLUEPRINT_OPENCODE_COMMAND, BLUEPRINT_CODEX_COMMAND, or BLUEPRINT_CLAUDE_CODE_COMMAND."
            ),
        )

    def _is_worker_configured(self, worker_type: str) -> bool:
        adapter = self.registry.get(worker_type)
        if adapter is None:
            return False
        checker = getattr(adapter, "is_configured", None)
        if callable(checker):
            return bool(checker(self.project_service.settings))
        return True

    def _new_run_id(self, runs: list[RunRecord]) -> str:
        existing_ids = {run.run_id for run in runs}
        while True:
            candidate = f"run_{uuid4().hex[:12]}"
            if candidate not in existing_ids:
                return candidate

    def _get_work_item(self, project_id: str, card_id: str) -> dict:
        work_order = self.flow_service.get_work_order(project_id)
        return next(item for item in work_order["work_items"] if item["card_id"] == card_id)

    def _commit_run_stage(self, project_id: str, run_id: str, stage: str) -> None:
        if stage == "reviewed":
            message = f"Materialize reviewed run {run_id}"
        elif stage == "cleanup":
            message = f"Cleanup run {run_id}"
        else:
            message = f"Run lifecycle {run_id}: {stage}"
        try:
            self.project_service.git_service(project_id).commit(message)
        except Exception as exc:
            logger.exception("Git commit failed for project=%s run=%s stage=%s", project_id, run_id, stage)
            self._mark_project_needs_git_repair(project_id, f"Git commit failed for {stage} on {run_id}: {exc}")
            try:
                graph = self.project_service.graph_store(project_id).load_graph()
                run = next((item for item in graph.runs if item.run_id == run_id), None)
                if run is None:
                    return
                self._append_event(
                    project_id,
                    run_id,
                    run.card_id,
                    event_type="git_commit_failed",
                    message=f"Git commit failed for {stage}: {exc}",
                )
            except Exception:
                logger.exception("Failed to persist git commit failure event for project=%s run=%s", project_id, run_id)

    def _mark_project_needs_git_repair(self, project_id: str, reason: str) -> None:
        root = self.project_service.project_path(project_id)
        try:
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            graph.metadata["needs_git_repair"] = {
                "reason": reason,
                "updated_at": utc_now(),
            }
            store.save_graph(graph)
        except Exception:
            logger.exception("Failed to mark project %s as needing git repair", project_id)
            try:
                atomic_write_json(
                    root / "project_recovery_required.json",
                    {
                        "reason": f"{reason}; failed to update graph metadata",
                        "created_at": utc_now(),
                    },
                )
            except Exception:
                logger.exception("Failed to write recovery marker for project=%s", project_id)

    def _reconcile_active_runs(self) -> None:
        for child in sorted(self.project_service.settings.data_root.iterdir()):
            if not child.is_dir():
                continue
            project_id = child.name
            lock = self.project_service.lock_for(project_id)
            with lock:
                store = self.project_service.graph_store(project_id)
                try:
                    cards = store.load_cards()
                    graph = store.load_graph()
                except Exception as exc:
                    logger.exception("Failed to reconcile project %s", project_id)
                    try:
                        atomic_write_json(
                            child / "project_recovery_required.json",
                            {
                                "reason": f"Project failed to load during active-run reconcile: {exc}",
                                "created_at": utc_now(),
                            },
                        )
                    except Exception:
                        logger.exception("Failed to write reconcile recovery marker for project=%s", project_id)
                    continue
                card_map = {card.card_id: card for card in cards}
                changed = False
                for run in graph.runs:
                    if run.status not in {"queued", "running", "reviewing"}:
                        continue
                    thread = self._threads.get(run.run_id)
                    if thread and thread.is_alive():
                        continue
                    run.status = "failed"
                    run.summary = "Backend restarted before executor completed; run marked failed during reconcile."
                    run.finished_at = utc_now()
                    card = card_map.get(run.card_id)
                    if card and card.status in {"running", "reviewing"}:
                        card.status = "failed"
                        card.progress_note = None
                        card.manager_review = run.summary
                        ModuleGroupStateService.sync_linked_module_status_from_card(card, graph.modules)
                    events = store.load_run_events(run.run_id)
                    events.append(
                        RunEvent(
                            event_id=f"evt_{run.run_id}_{len(events) + 1:03d}",
                            run_id=run.run_id,
                            card_id=run.card_id,
                            source="manager",
                            event_type="run_reconciled",
                            visibility="bubble",
                            preview_id=f"bubble_{run.card_id}",
                            utterance_id=f"utt_{run.run_id}_{len(events) + 1:03d}",
                            stream_state="complete",
                            message=run.summary,
                            created_at=utc_now(),
                        )
                    )
                    store.save_run_events(run.run_id, events)
                    changed = True
                if changed:
                    ModuleGroupStateService.sync_group_hierarchy(cards, graph.modules)
                    store.save_graph(graph)
                    store.save_cards(cards)

    @staticmethod
    def _infer_output_spec(card_id: str, run_id: str, output: CardAssetRef, index: int) -> tuple[str, str, str]:
        raw_name = output.asset_id or output.label or f"output_{index:02d}"
        lowered = raw_name.lower()
        role = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_") or f"output_{index:02d}"
        if any(token in lowered for token in ("plot", "figure", "heatmap", "volcano", "preview", "image", "svg")):
            output_type = "figure"
            suffix = "svg"
        elif any(token in lowered for token in ("summary", "report", "markdown", "readme", "note")):
            output_type = "markdown"
            suffix = "md"
        elif lowered.endswith(".json") or "json" in lowered:
            output_type = "json"
            suffix = "json"
        else:
            output_type = "table"
            suffix = "tsv"
        return role, output_type, f"results/{card_id}/{run_id}/{role}.{suffix}"
