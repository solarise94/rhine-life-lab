from __future__ import annotations

import logging
from threading import Event, Lock, Thread
from uuid import uuid4

from app.models.chat import ChatRequest, ChatSessionMessage, ChatSessionMessageTimelineItem
from app.models.manager_auto import ManagerWakeEvent
from app.services.chat_stream_relay import ChatStreamRelay
from app.services.chat_session_service import ChatSessionService
from app.services.manager_auto_service import ManagerAutoService
from app.services.manager_service import ManagerService
from app.services.manager_wake_service import ManagerWakeService
from app.services.project_service import ProjectService
from app.services.utils import utc_now


logger = logging.getLogger(__name__)

WAKE_POLL_INTERVAL_SECONDS = 5.0


class ManagerWakeProcessor:
    def __init__(
        self,
        project_service: ProjectService,
        manager_auto_service: ManagerAutoService,
        manager_wake_service: ManagerWakeService,
        chat_session_service: ChatSessionService,
        manager_service: ManagerService,
    ) -> None:
        self.project_service = project_service
        self.manager_auto_service = manager_auto_service
        self.manager_wake_service = manager_wake_service
        self.chat_session_service = chat_session_service
        self.manager_service = manager_service
        self.chat_stream_relay = ChatStreamRelay(chat_session_service, manager_service)
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._project_locks: dict[str, Lock] = {}
        self._processor_id = f"processor_{uuid4().hex[:12]}"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._loop, name="manager-wake-processor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    def _loop(self) -> None:
        while not self._stop_event.wait(WAKE_POLL_INTERVAL_SECONDS):
            for project in self.project_service.list_projects():
                lock = self._lock_for(project.project_id)
                if not lock.acquire(blocking=False):
                    continue
                try:
                    self._process_project(project.project_id)
                finally:
                    lock.release()

    def _process_project(self, project_id: str) -> None:
        wake_event = self.manager_wake_service.claim_next(project_id, processor_id=self._processor_id)
        if wake_event is None:
            return
        auto_state = self.manager_auto_service.get_state(project_id)
        owner_session_id = auto_state.owner_session_id
        if not auto_state.enabled or not owner_session_id:
            self.manager_wake_service.mark_skipped(project_id, wake_event.wake_id, "Auto mode is disabled.")
            return
        if self._is_provider_api_failure_wake(wake_event):
            message = (
                f"因外部 API 错误，自动运行已停止，等待服务商或网络恢复后再继续。"
                f"事件：{wake_event.message}"
            )
            self.manager_auto_service.stop(
                project_id,
                owner_session_id,
                reason="provider_api_error",
                message=message,
            )
            self.chat_session_service.append_messages(
                project_id,
                owner_session_id,
                [self._system_message(f"auto_stop_provider_api_error_{wake_event.wake_id}", message)],
            )
            self.manager_wake_service.mark_done(project_id, wake_event.wake_id)
            return
        try:
            self.manager_auto_service.set_runtime_state(
                project_id,
                state_value="thinking",
                last_wake_id=wake_event.wake_id,
                active_run_id=wake_event.run_id or auto_state.active_run_id,
                active_job_id=wake_event.job_id or auto_state.active_job_id,
            )
            workboard_service = self.manager_auto_service.background_workboard_service
            workboard_snapshot = (
                workboard_service.signal_snapshot(project_id, session_id=owner_session_id)
                if workboard_service is not None
                else {"counts": {}, "has_actionable": False}
            )
            if wake_event.kind == "workboard_actionable" and not workboard_snapshot.get("has_actionable"):
                self.manager_auto_service.evaluate_workboard_and_maybe_signal(project_id, owner_session_id)
                self.manager_wake_service.mark_done(project_id, wake_event.wake_id)
                return
            self.chat_session_service.append_messages(
                project_id,
                owner_session_id,
                [self._wake_notice_message(wake_event)],
                dedupe_ids=[f"wake_notice_{wake_event.wake_id}"],
            )
            pending_directives = self.manager_auto_service.pending_directives(project_id)
            request = ChatRequest(
                message=self._wake_prompt(wake_event, pending_directives, workboard_snapshot.get("counts") or {}),
                session_id=owner_session_id,
                thinking_effort="medium",
                messages=[],
            )
            self.chat_stream_relay.run_to_session(
                project_id,
                owner_session_id,
                request,
                message_id=f"wake_response_{wake_event.wake_id}",
                initial_thinking="Manager 正在处理 AUTO 唤醒…",
            )
            if pending_directives:
                self.manager_auto_service.resolve_directives(
                    project_id,
                    [item.id for item in pending_directives],
                    status="consumed",
                    note=f"Handled with wake {wake_event.wake_id}",
                )
            self.manager_auto_service.set_runtime_state(project_id, increment_chain=True)
            latest_state = self.manager_auto_service.evaluate_workboard_and_maybe_signal(project_id, owner_session_id, from_turn_settlement=True)
            if auto_state.mode == "once":
                self.manager_auto_service.stop(project_id, owner_session_id, reason="auto_once_complete", message="Auto once 已完成，已退出 auto 模式。")
                self.chat_session_service.append_messages(
                    project_id,
                    owner_session_id,
                    [self._system_message("auto_once_complete", "Auto once 已完成，已退出 auto 模式。")],
                    dedupe_ids=["auto_once_complete"],
                )
            self.manager_wake_service.mark_done(project_id, wake_event.wake_id)
        except Exception as exc:
            logger.exception("Manager wake processing failed for project=%s wake=%s", project_id, wake_event.wake_id)
            self._settle_failed_stream_message(project_id, owner_session_id, wake_event.wake_id)
            self.manager_wake_service.mark_failed(project_id, wake_event.wake_id, str(exc))
            self.manager_auto_service.stop(
                project_id,
                owner_session_id,
                reason="wake_processing_failed",
                message=f"因自动调度失败，任务停止，已退出 auto 模式。原因：{exc}",
            )
            self.chat_session_service.append_messages(
                project_id,
                owner_session_id,
                [self._system_message("auto_stop_error", f"因自动调度失败，任务停止，已退出 auto 模式。原因：{exc}")],
                dedupe_ids=[f"auto_stop_error_{wake_event.wake_id}"],
            )

    def _settle_failed_stream_message(self, project_id: str, session_id: str, wake_id: str) -> None:
        try:
            self.chat_stream_relay.settle_message(project_id, session_id, f"wake_response_{wake_id}", "error")
        except Exception:
            logger.exception("Failed to settle auto wake stream message for project=%s wake=%s", project_id, wake_id)

    @staticmethod
    def _is_provider_api_failure_wake(event: ManagerWakeEvent) -> bool:
        if event.kind != "executor_validation_failed":
            return False
        payload = event.payload_summary if isinstance(event.payload_summary, dict) else {}
        reviewer = payload.get("reviewer")
        if isinstance(reviewer, dict) and reviewer.get("mode") == "reviewer_api_error":
            return True
        issues = payload.get("issues")
        if isinstance(issues, list):
            return any(isinstance(issue, dict) and issue.get("code") == "reviewer_api_error" for issue in issues)
        return False

    def _wake_notice_message(self, event: ManagerWakeEvent) -> ChatSessionMessage:
        content = f"后台事件：{event.message}"
        return ChatSessionMessage(
            id=f"wake_notice_{event.wake_id}",
            role="manager",
            content=content,
            state="done",
            timeline=[
                ChatSessionMessageTimelineItem(
                    id=f"wake_notice_{event.wake_id}_text",
                    kind="text",
                    content=content,
                    status="done",
                )
            ],
        )

    @staticmethod
    def _system_message(message_id: str, content: str) -> ChatSessionMessage:
        return ChatSessionMessage(
            id=message_id,
            role="manager",
            content=content,
            state="done",
            timeline=[ChatSessionMessageTimelineItem(id=f"{message_id}_text", kind="text", content=content, status="done")],
        )

    @staticmethod
    def _wake_prompt(event: ManagerWakeEvent, directives: list[object], workboard_counts: dict[str, object]) -> str:
        directive_text = "\n".join(f"- {getattr(item, 'text', '')}" for item in directives if getattr(item, "text", None))
        lines = [
            "Auto mode background signal received.",
            f"kind: {event.kind}",
            f"message: {event.message}",
        ]
        if workboard_counts:
            lines.append(f"workboard_counts: {workboard_counts}")
        if event.card_id:
            lines.append(f"card_id: {event.card_id}")
        if event.run_id:
            lines.append(f"run_id: {event.run_id}")
        if event.job_id:
            lines.append(f"job_id: {event.job_id}")
        if directive_text:
            lines.extend(["pending_directives:", directive_text])
        lines.append("Call get_background_workboard first. Consume at most one actionable workboard item or one claimed run batch in this turn.")
        lines.append("If you start background work, stop after reporting ids and let async-boundary yield the turn.")
        return "\n".join(lines)

    def _lock_for(self, project_id: str) -> Lock:
        if project_id not in self._project_locks:
            self._project_locks[project_id] = Lock()
        return self._project_locks[project_id]
