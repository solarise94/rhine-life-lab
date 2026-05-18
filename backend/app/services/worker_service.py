from __future__ import annotations

from pathlib import Path
from threading import Thread
import subprocess

from app.models.cards import Card, CardAssetRef
from app.models.graph import Asset, Claim, Module, ReportItem, RunRecord
from app.models.runs import ExpectedOutput, RunEvent, TaskPacket, TaskPacketAsset
from app.services.manifest_service import ManifestService
from app.services.project_service import ProjectService
from app.services.runtime_approval_service import RuntimeApprovalService
from app.services.utils import atomic_write_json, utc_now
from app.workers import build_worker_registry


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
        self.registry = build_worker_registry()
        self._threads: dict[str, Thread] = {}

    def start_run(self, project_id: str, card_id: str, worker_type: str | None = None) -> dict:
        worker_type = worker_type or self.project_service.settings.default_worker_type
        adapter = self.registry.get(worker_type)
        if adapter is None:
            raise ValueError(f"Unknown worker_type: {worker_type}")

        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            graph = store.load_graph()
            card = next(item for item in cards if item.card_id == card_id)
            run_id = f"run_{len(graph.runs) + 1:03d}"
            run_dir = self.project_service.project_path(project_id) / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            packet = self._task_packet(project_id, run_id, card, graph.assets)
            atomic_write_json(run_dir / "task_packet.json", packet.model_dump())
            launch_spec = adapter.build_launch_spec(
                packet=packet,
                packet_path=run_dir / "task_packet.json",
                run_dir=run_dir,
                project_root=self.project_service.project_path(project_id),
                settings=self.project_service.settings,
            )
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
            (run_dir / "transcript.md").write_text(f"# {run_id}\n\nRun created.\n", encoding="utf-8")
            (run_dir / "commands.log").write_text(" ".join(launch_spec.command) + "\n", encoding="utf-8")
            graph.runs.append(
                RunRecord(
                    run_id=run_id,
                    card_id=card_id,
                    module_id=card.linked_modules[0] if card.linked_modules else None,
                    status="needs_approval" if unresolved else "queued",
                    title=f"{card.title} 执行",
                    summary="等待执行器启动。" if not unresolved else "等待用户确认运行期权限请求。",
                    started_at=utc_now(),
                    finished_at=None,
                    worker_type=worker_type,
                )
            )
            card.status = "running"
            card.progress_note = "执行器已创建，等待运行。"
            if run_id not in card.linked_runs:
                card.linked_runs.append(run_id)
            store.save_graph(graph)
            store.save_cards(cards)
            store.save_run_events(
                run_id,
                [
                    RunEvent(
                        event_id=f"evt_{run_id}_001",
                        run_id=run_id,
                        card_id=card_id,
                        source="manager",
                        event_type="run_created",
                        visibility="bubble",
                        preview_id=f"bubble_{card_id}",
                        utterance_id=f"utt_{run_id}_001",
                        stream_state="complete",
                        message=f"已创建 run {run_id}，worker={worker_type}。",
                        created_at=utc_now(),
                    )
                ],
            )
            for index, decision in enumerate(approvals, start=2):
                self._append_event(
                    project_id,
                    run_id,
                    card_id,
                    event_type="permission_decision",
                    message=f"[{decision['risk_level']}] {decision['target']} -> {decision['decision']}: {decision['reason']}",
                    sequence_hint=index,
                )

        self.project_service.git_service(project_id).commit(f"Create run {run_id}")
        if unresolved:
            return {
                "run_id": run_id,
                "card_id": card_id,
                "worker_type": worker_type,
                "status": "needs_approval",
                "pending_approvals": unresolved,
            }

        thread = Thread(
            target=self._execute_run,
            kwargs={
                "project_id": project_id,
                "run_id": run_id,
                "card_id": card_id,
                "worker_type": worker_type,
                "command": launch_spec.command,
                "cwd": launch_spec.cwd,
                "environment": launch_spec.environment,
            },
            daemon=True,
        )
        self._threads[run_id] = thread
        thread.start()
        return {"run_id": run_id, "card_id": card_id, "worker_type": worker_type, "status": "queued"}

    def continue_run_after_approval(self, project_id: str, run_id: str) -> dict:
        unresolved = self.runtime_approval_service.unresolved_user_requests(project_id, run_id)
        if unresolved:
            raise ValueError("Run still has unresolved approval requests.")
        store = self.project_service.graph_store(project_id)
        graph = store.load_graph()
        run = next(item for item in graph.runs if item.run_id == run_id)
        if run.status != "needs_approval":
            return {"run_id": run_id, "status": run.status}
        packet = self.manifest_service.load_task_packet(project_id, run_id)
        adapter = self.registry.get(run.worker_type)
        if adapter is None:
            raise ValueError(f"Unknown worker_type: {run.worker_type}")
        run_dir = self.project_service.project_path(project_id) / "runs" / run_id
        launch_spec = adapter.build_launch_spec(
            packet=packet,
            packet_path=run_dir / "task_packet.json",
            run_dir=run_dir,
            project_root=self.project_service.project_path(project_id),
            settings=self.project_service.settings,
        )
        thread = Thread(
            target=self._execute_run,
            kwargs={
                "project_id": project_id,
                "run_id": run_id,
                "card_id": run.card_id,
                "worker_type": run.worker_type,
                "command": launch_spec.command,
                "cwd": launch_spec.cwd,
                "environment": launch_spec.environment,
            },
            daemon=True,
        )
        self._threads[run_id] = thread
        thread.start()
        return {"run_id": run_id, "status": "queued"}

    def review_run(self, project_id: str, run_id: str, accept: bool = True) -> dict:
        valid, errors = self.manifest_service.validate_manifest(project_id, run_id)
        if accept and not valid:
            raise ValueError("; ".join(errors))
        review_context = self.manifest_service.manifest_to_review_context(project_id, run_id)
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            graph = store.load_graph()
            run = next(item for item in graph.runs if item.run_id == run_id)
            card = next(item for item in cards if item.card_id == run.card_id)
            self._supersede_previous_outputs(card, graph.assets, graph.claims, run_id)
            created_assets = self._materialize_run_assets(
                graph=graph,
                run_id=run_id,
                card=card,
                created_assets=review_context.created_assets,
                status="valid" if accept else "candidate",
                input_asset_ids=[item.asset_id for item in self.manifest_service.load_manifest(project_id, run_id).inputs_used],
            )
            if accept:
                new_claim_ids = self._materialize_claims(graph, run_id, review_context.key_findings, [asset.asset_id for asset in created_assets])
                card.status = "accepted"
                card.progress_note = None
                card.manager_review = "结果已通过 manifest 校验并被 Manager 接受。"
                card.key_findings = review_context.key_findings or ["结果已生成并完成审核。"]
                self._attach_assets_to_card(card, created_assets)
                self._sync_card_outputs(card, created_assets)
                self._mark_linked_modules(card, graph.modules, "accepted")
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
                card.manager_review = "Manager 拒绝了这次运行结果，产出已保留为 candidate。"
            run.summary = review_context.summary
            run.finished_at = utc_now()
            store.save_graph(graph)
            store.save_cards(cards)

        self._append_event(
            project_id,
            run_id,
            run.card_id,
            event_type="manager_review",
            message="Manager 已接受运行结果。" if accept else "Manager 已拒绝运行结果，保留 candidate 产物。",
        )
        self.project_service.git_service(project_id).commit(f"Review run {run_id}")
        return {"run_id": run_id, "accepted": accept}

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
    ) -> None:
        self._set_run_status(project_id, run_id, card_id, status="running", summary="执行器已启动。", progress_note="正在执行分析任务。")
        self._append_event(project_id, run_id, card_id, event_type="run_started", message=f"执行器 {worker_type} 已启动。")
        transcript_path = self.project_service.project_path(project_id) / "runs" / run_id / "transcript.md"

        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

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

        if timed_out:
            message = "执行超时，已终止。"
            self._append_event(project_id, run_id, card_id, event_type="run_failed", message=message)
            self._set_run_status(project_id, run_id, card_id, status="failed", summary=message, progress_note=None)
            self.project_service.git_service(project_id).commit(f"Fail run {run_id}")
            return

        if return_code != 0:
            message = f"执行器退出码 {return_code}。"
            self._append_event(project_id, run_id, card_id, event_type="run_failed", message=message)
            self._set_run_status(project_id, run_id, card_id, status="failed", summary=message, progress_note=None)
            self.project_service.git_service(project_id).commit(f"Fail run {run_id}")
            return

        valid, errors = self.manifest_service.validate_manifest(project_id, run_id)
        if not valid:
            message = "Manifest 校验失败：" + "; ".join(errors)
            self._append_event(project_id, run_id, card_id, event_type="run_failed", message=message)
            self._set_run_status(project_id, run_id, card_id, status="failed", summary=message, progress_note=None)
            self.project_service.git_service(project_id).commit(f"Fail run {run_id}")
            return

        manifest = self.manifest_service.load_manifest(project_id, run_id)
        self._append_event(project_id, run_id, card_id, event_type="run_completed", message="结果文件与 manifest 已生成，等待 Manager 审核。")
        self._set_run_status(
            project_id,
            run_id,
            card_id,
            status="success",
            summary=manifest.summary,
            progress_note="结果已生成，等待 Manager 审核。",
            card_status="needs_review",
        )
        self.project_service.git_service(project_id).commit(f"Complete run {run_id}")

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
            run = next(item for item in graph.runs if item.run_id == run_id)
            card = next(item for item in cards if item.card_id == card_id)
            run.status = status
            run.summary = summary
            if status in {"success", "failed", "cancelled"}:
                run.finished_at = utc_now()
            if card_status:
                card.status = card_status
            elif status == "failed":
                card.status = "failed"
                card.manager_review = summary
            card.progress_note = progress_note
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
                    source="executor" if event_type.startswith("run_") or event_type == "executor_output" else "manager",
                    event_type=event_type,
                    visibility="bubble",
                    preview_id=f"bubble_{card_id}",
                    utterance_id=f"utt_{run_id}_{sequence:03d}",
                    stream_state="complete",
                    message=message,
                    created_at=utc_now(),
                )
            )
            store.save_run_events(run_id, events)

    def _task_packet(self, project_id: str, run_id: str, card: Card, assets: list[Asset]) -> TaskPacket:
        input_assets = [
            TaskPacketAsset(asset_id=asset.asset_id, path=asset.path, type=asset.asset_type)
            for asset in assets
            if asset.asset_id in card.linked_assets
        ]
        expected_outputs = [
            ExpectedOutput(role="summary", path_hint=f"results/{card.card_id}/{run_id}/summary.md"),
            ExpectedOutput(role="table", path_hint=f"results/{card.card_id}/{run_id}/result.tsv"),
            ExpectedOutput(role="plot", path_hint=f"results/{card.card_id}/{run_id}/preview.svg"),
        ]
        return TaskPacket(
            task_id=run_id,
            project_id=project_id,
            card_id=card.card_id,
            goal=card.summary,
            input_assets=input_assets,
            expected_outputs=expected_outputs,
            allowed_paths=[f"runs/{run_id}/", f"results/{card.card_id}/{run_id}/", "scripts/generated/"],
            readonly_paths=[asset.path for asset in input_assets],
            forbidden_paths=[".git/", "graph/"],
            constraints=[
                "Do not overwrite existing valid assets.",
                f"Write outputs under results/{card.card_id}/{run_id}/",
                f"Write manifest to runs/{run_id}/manifest.json.",
            ],
            worker_instructions="You are a bioinformatics worker agent. Produce a complete manifest.",
        )

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
    def _sync_card_outputs(card: Card, assets: list[Asset]) -> None:
        output_map = {item.asset_id for item in card.outputs if item.asset_id}
        for asset in assets:
            if asset.asset_id not in output_map:
                card.outputs.append(CardAssetRef(label=asset.title, asset_id=asset.asset_id))

    @staticmethod
    def _mark_linked_modules(card: Card, modules: list[Module], status: str) -> None:
        for module in modules:
            if module.module_id in card.linked_modules:
                module.status = status

    @staticmethod
    def _supersede_previous_outputs(card: Card, assets: list[Asset], claims: list[Claim], current_run_id: str) -> None:
        current_asset_ids = set(card.linked_assets)
        for asset in assets:
            if asset.asset_id in current_asset_ids and asset.created_by_run and asset.created_by_run != current_run_id and asset.status == "valid":
                asset.status = "superseded"
        stale_assets = {asset.asset_id for asset in assets if asset.status == "superseded"}
        for claim in claims:
            if stale_assets.intersection(claim.depends_on_assets) and claim.status == "valid":
                claim.status = "superseded"
