from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any
from threading import Event, Lock, Thread
from uuid import uuid4

from app.models.chat import ChatResponse, ChatSessionMessage, ChatSessionMessageTimelineItem, ChatRequest, ChatTokenUsage
from app.models.manager_auto import ManagerWakeEvent
from app.models.patches import Proposal
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
        try:
            TERMINAL_RUN_WAKE_KINDS = {
                "card_run_reviewed",
                "card_run_failed",
                "card_run_cancelled",
                "manifest_validation_failed",
                "executor_validation_failed",
                "runtime_dependency_missing",
                "run_filesystem_audit_failed",
            }
            clear_active_run = wake_event.kind in TERMINAL_RUN_WAKE_KINDS
            clear_active_job = wake_event.kind in {
                "runtime_dependency_install_succeeded",
                "runtime_dependency_install_failed",
            }
            self.manager_auto_service.set_runtime_state(
                project_id,
                state_value="thinking",
                last_wake_id=wake_event.wake_id,
                clear_active_run=clear_active_run,
                clear_active_job=clear_active_job,
                active_run_id=None if clear_active_run else (wake_event.run_id or auto_state.active_run_id),
                active_job_id=None if clear_active_job else (wake_event.job_id or auto_state.active_job_id),
            )
            self.chat_session_service.append_messages(
                project_id,
                owner_session_id,
                [self._wake_notice_message(wake_event)],
                dedupe_ids=[f"wake_notice_{wake_event.wake_id}"],
            )
            pending_directives = self.manager_auto_service.pending_directives(project_id)
            request = ChatRequest(
                message=self._wake_prompt(wake_event, pending_directives),
                session_id=owner_session_id,
                thinking_effort="medium",
                messages=[],
            )
            self._stream_manager_response(project_id, owner_session_id, wake_event, request)
            if pending_directives:
                self.manager_auto_service.resolve_directives(
                    project_id,
                    [item.id for item in pending_directives],
                    status="consumed",
                    note=f"Handled with wake {wake_event.wake_id}",
                )
            latest_state = self.manager_auto_service.get_state(project_id)
            next_state_value = "running" if latest_state.active_run_id or latest_state.active_job_id else "idle"
            self.manager_auto_service.set_runtime_state(project_id, state_value=next_state_value, increment_chain=True)
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
            session = self.chat_session_service.get_session(project_id, session_id)
            message = next((item for item in session.messages if item.id == f"wake_response_{wake_id}"), None)
            if message and message.state not in {"done", "error"}:
                self.chat_session_service.upsert_message(project_id, session_id, self._settle_stream_message(message, "error"))
        except Exception:
            logger.exception("Failed to settle auto wake stream message for project=%s wake=%s", project_id, wake_id)

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

    def _stream_manager_response(
        self,
        project_id: str,
        session_id: str,
        event: ManagerWakeEvent,
        request: ChatRequest,
    ) -> ChatResponse:
        message = self._initial_stream_message(event)
        self.chat_session_service.upsert_message(project_id, session_id, message)
        saw_response = False
        last_persisted_at = time.monotonic()
        for payload in self._iter_stream_payloads(project_id, request):
            message = self._apply_stream_payload(message, payload)
            event_type = payload.get("type")
            if event_type == "response":
                saw_response = True
            now = time.monotonic()
            if self._should_persist_stream_event(event_type, now, last_persisted_at):
                self.chat_session_service.upsert_message(project_id, session_id, message)
                last_persisted_at = now
            if event_type == "error":
                raise RuntimeError(str(payload.get("detail") or "Manager stream failed."))
        if message.state not in {"done", "error"}:
            message = self._settle_stream_message(message, "done")
        if not saw_response and not message.content.strip():
            raise RuntimeError("Manager stream ended without a response.")
        self.chat_session_service.upsert_message(project_id, session_id, message)
        return ChatResponse(message=message.content, thinking=message.thinking, metadata={"token_usage": message.token_usage.model_dump() if message.token_usage else None})

    @staticmethod
    def _should_persist_stream_event(event_type: object, now: float, last_persisted_at: float) -> bool:
        if event_type in {
            "thinking_start",
            "thinking_end",
            "tool_start",
            "tool_end",
            "tool_report",
            "proposal",
            "response",
            "done",
            "error",
            "usage",
        }:
            return True
        return now - last_persisted_at >= 0.75

    def _iter_stream_payloads(self, project_id: str, request: ChatRequest) -> Iterator[dict[str, Any]]:
        buffer = ""
        for chunk in self.manager_service.stream_chat(project_id, request):
            buffer += chunk.decode("utf-8", errors="replace")
            while True:
                boundary = self._sse_boundary(buffer)
                if boundary is None:
                    break
                raw_event = buffer[: boundary[0]]
                buffer = buffer[boundary[1] :]
                payload = self._parse_sse_payload(raw_event)
                if payload is not None:
                    yield payload
        if buffer.strip():
            payload = self._parse_sse_payload(buffer)
            if payload is not None:
                yield payload

    @staticmethod
    def _sse_boundary(buffer: str) -> tuple[int, int] | None:
        boundaries = [(index, index + 2) for index in [buffer.find("\n\n")] if index >= 0]
        crlf_index = buffer.find("\r\n\r\n")
        if crlf_index >= 0:
            boundaries.append((crlf_index, crlf_index + 4))
        return min(boundaries, key=lambda item: item[0]) if boundaries else None

    @staticmethod
    def _parse_sse_payload(raw_event: str) -> dict[str, Any] | None:
        lines = []
        for line in raw_event.splitlines():
            if line.startswith("data:"):
                lines.append(line[5:].lstrip())
        if not lines:
            return None
        payload = "\n".join(lines).strip()
        if not payload:
            return None
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid Manager stream event: {exc}") from exc
        if not isinstance(parsed, dict):
            return None
        return parsed

    def _initial_stream_message(self, event: ManagerWakeEvent) -> ChatSessionMessage:
        started_at = self._now_ms()
        content = "Manager 正在处理 AUTO 唤醒…"
        return ChatSessionMessage(
            id=f"wake_response_{event.wake_id}",
            role="manager",
            content="",
            thinking=content,
            state="thinking",
            timeline=[
                ChatSessionMessageTimelineItem(
                    id=f"wake_response_{event.wake_id}_thinking_hb",
                    kind="thinking",
                    content=content,
                    status="running",
                    started_at=started_at,
                )
            ],
        )

    def _apply_stream_payload(self, message: ChatSessionMessage, payload: dict[str, Any]) -> ChatSessionMessage:
        event_type = str(payload.get("type") or "")
        next_message = message.model_copy(deep=True)
        timeline = list(next_message.timeline or [])
        if event_type == "thinking_start":
            item_id = self._timeline_item_id("thinking", payload, str(len(timeline)))
            timeline = [item for item in timeline if not item.id.endswith("_thinking_hb")]
            existing = self._find_timeline_item(timeline, item_id)
            timeline = self._upsert_timeline_item(
                timeline,
                ChatSessionMessageTimelineItem(
                    id=item_id,
                    kind="thinking",
                    content=existing.content if existing else "",
                    status="running",
                    started_at=existing.started_at if existing else self._event_number(payload, "started_at") or self._now_ms(),
                ),
            )
            next_message.state = "streaming" if next_message.content else "thinking"
        elif event_type == "thinking_delta":
            item_id = self._timeline_item_id("thinking", payload, self._running_item_id(timeline, "thinking") or str(len(timeline)))
            delta = str(payload.get("delta") or "")
            existing = self._find_timeline_item(timeline, item_id)
            content = f"{existing.content if existing and existing.content else ''}{delta}"
            timeline = self._upsert_timeline_item(
                timeline,
                ChatSessionMessageTimelineItem(
                    id=item_id,
                    kind="thinking",
                    content=content,
                    status="running",
                    started_at=existing.started_at if existing else self._now_ms(),
                ),
            )
            next_message.thinking = f"{next_message.thinking or ''}{delta}"
            next_message.state = "streaming" if next_message.content else "thinking"
        elif event_type == "thinking_end":
            item_id = self._timeline_item_id("thinking", payload, self._running_item_id(timeline, "thinking") or str(len(timeline)))
            existing = self._find_timeline_item(timeline, item_id)
            content = str(payload.get("content") or (existing.content if existing else "") or next_message.thinking or "")
            timeline = self._upsert_timeline_item(
                timeline,
                ChatSessionMessageTimelineItem(
                    id=item_id,
                    kind="thinking",
                    content=content,
                    status="done",
                    started_at=(existing.started_at if existing else self._event_number(payload, "started_at")) or self._now_ms(),
                    ended_at=self._event_number(payload, "ended_at") or self._now_ms(),
                ),
            )
            if content:
                next_message.thinking = content
        elif event_type == "heartbeat":
            content = next_message.thinking or str(payload.get("message") or "Manager 正在生成回复…")
            running_id = self._running_item_id(timeline, "thinking")
            item_id = running_id or f"{next_message.id}_thinking_hb"
            existing = self._find_timeline_item(timeline, item_id)
            timeline = self._upsert_timeline_item(
                timeline,
                ChatSessionMessageTimelineItem(
                    id=item_id,
                    kind="thinking",
                    content=content,
                    status="running",
                    started_at=existing.started_at if existing else self._now_ms(),
                ),
            )
            next_message.thinking = content
            next_message.state = "streaming" if next_message.content else "thinking"
        elif event_type == "text_delta":
            item_id = self._timeline_item_id("text", payload, self._last_text_id(timeline) or str(len(timeline)))
            delta = str(payload.get("delta") or "")
            existing = self._find_timeline_item(timeline, item_id)
            timeline = self._upsert_timeline_item(
                self._settle_running_timeline(timeline, "text", "done", exclude_id=item_id),
                ChatSessionMessageTimelineItem(
                    id=item_id,
                    kind="text",
                    content=f"{existing.content if existing and existing.content else ''}{delta}",
                    status="running",
                ),
            )
            next_message.content = f"{next_message.content}{delta}"
            next_message.state = "streaming"
        elif event_type == "usage":
            usage = payload.get("usage")
            if isinstance(usage, dict):
                next_message.token_usage = ChatTokenUsage.model_validate(usage)
        elif event_type == "tool_start":
            item_id = str(payload.get("tool_call_id") or self._timeline_item_id("tool", payload, f"{payload.get('tool_name') or 'unknown'}_{len(timeline)}"))
            existing = self._find_timeline_item(timeline, item_id)
            timeline = self._upsert_timeline_item(
                self._settle_running_timeline(timeline, "text", "done"),
                ChatSessionMessageTimelineItem(
                    id=item_id,
                    kind="tool",
                    label=str(payload.get("label") or payload.get("done_label") or payload.get("tool_name") or "Tool"),
                    tool_name=str(payload.get("tool_name") or "") or None,
                    status="running",
                    started_at=existing.started_at if existing else self._now_ms(),
                ),
            )
            next_message.state = "streaming" if next_message.content else "thinking"
        elif event_type == "tool_end":
            item_id = str(payload.get("tool_call_id") or self._last_matching_tool_id(timeline, payload.get("tool_name")) or self._timeline_item_id("tool", payload, f"{payload.get('tool_name') or 'unknown'}_{len(timeline)}"))
            existing = self._find_timeline_item(timeline, item_id)
            is_error = bool(payload.get("is_error"))
            timeline = self._upsert_timeline_item(
                timeline,
                ChatSessionMessageTimelineItem(
                    id=item_id,
                    kind="tool",
                    content=existing.content if existing else None,
                    label=str(payload.get("done_label") or payload.get("label") or payload.get("tool_name") or "Tool"),
                    tool_name=str(payload.get("tool_name") or "") or None,
                    status="error" if is_error else "done",
                    started_at=existing.started_at if existing else self._now_ms(),
                    ended_at=self._now_ms(),
                ),
            )
        elif event_type == "tool_report":
            item_id = str(payload.get("tool_call_id") or self._last_matching_tool_id(timeline, payload.get("tool_name")) or self._timeline_item_id("tool", payload, f"{payload.get('tool_name') or 'unknown'}_{len(timeline)}"))
            existing = self._find_timeline_item(timeline, item_id)
            timeline = self._upsert_timeline_item(
                timeline,
                ChatSessionMessageTimelineItem(
                    id=item_id,
                    kind="tool",
                    content=str(payload.get("summary") or (existing.content if existing else "") or ""),
                    label=(existing.label if existing else None) or str(payload.get("tool_name") or "Tool"),
                    tool_name=str(payload.get("tool_name") or "") or None,
                    status="error" if existing and existing.status == "error" else "done",
                    started_at=existing.started_at if existing else self._now_ms(),
                    ended_at=existing.ended_at if existing else self._now_ms(),
                ),
            )
        elif event_type == "proposal":
            proposal = payload.get("proposal")
            if proposal:
                next_message.proposal = Proposal.model_validate(proposal)
        elif event_type == "response":
            response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
            content = str(response.get("message") or next_message.content)
            thinking = str(response.get("thinking") or next_message.thinking or "")
            next_message.content = content
            next_message.thinking = thinking or None
            proposal = response.get("proposal")
            if proposal:
                next_message.proposal = Proposal.model_validate(proposal)
            metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
            usage = metadata.get("token_usage")
            if isinstance(usage, dict):
                next_message.token_usage = ChatTokenUsage.model_validate(usage)
            timeline = self._finalize_response_timeline(timeline, next_message)
            next_message.state = "done"
        elif event_type == "done":
            next_message = self._settle_stream_message(next_message, "done")
            timeline = list(next_message.timeline or timeline)
        elif event_type == "error":
            next_message = self._settle_stream_message(next_message, "error")
            timeline = list(next_message.timeline or timeline)
            if not next_message.content:
                next_message.content = "请求失败。"
        next_message.timeline = timeline
        return next_message

    def _settle_stream_message(self, message: ChatSessionMessage, status: str) -> ChatSessionMessage:
        next_message = message.model_copy(deep=True)
        next_message.state = "error" if status == "error" else "done"
        next_message.timeline = [
            item.model_copy(update={"status": status, "ended_at": item.ended_at or self._now_ms()}) if item.status == "running" else item
            for item in (next_message.timeline or [])
        ]
        return next_message

    def _finalize_response_timeline(
        self,
        timeline: list[ChatSessionMessageTimelineItem],
        message: ChatSessionMessage,
    ) -> list[ChatSessionMessageTimelineItem]:
        next_timeline = self._settle_running_timeline(timeline, "tool", "done")
        next_timeline = self._settle_running_timeline(next_timeline, "thinking", "done")
        next_timeline = self._settle_running_timeline(next_timeline, "text", "done")
        if message.thinking and not any(item.kind == "thinking" and item.content for item in next_timeline):
            next_timeline.insert(
                0,
                ChatSessionMessageTimelineItem(
                    id=f"{message.id}_thinking_final",
                    kind="thinking",
                    content=message.thinking,
                    status="done",
                    started_at=self._now_ms(),
                    ended_at=self._now_ms(),
                ),
            )
        if message.content and not any(item.kind == "text" and item.content for item in next_timeline):
            next_timeline.append(
                ChatSessionMessageTimelineItem(
                    id=f"{message.id}_text_final",
                    kind="text",
                    content=message.content,
                    status="done",
                )
            )
        return next_timeline

    @staticmethod
    def _timeline_item_id(kind: str, payload: dict[str, Any], fallback: str) -> str:
        turn_index = payload.get("assistant_turn_index")
        content_index = payload.get("content_index")
        if content_index is None:
            if fallback.startswith(f"{kind}_"):
                return fallback
            return f"{kind}_{fallback}"
        prefix = f"{turn_index}_" if turn_index is not None else ""
        return f"{kind}_{prefix}{content_index}"

    @staticmethod
    def _find_timeline_item(
        timeline: list[ChatSessionMessageTimelineItem],
        item_id: str,
    ) -> ChatSessionMessageTimelineItem | None:
        return next((item for item in timeline if item.id == item_id), None)

    @staticmethod
    def _upsert_timeline_item(
        timeline: list[ChatSessionMessageTimelineItem],
        item: ChatSessionMessageTimelineItem,
    ) -> list[ChatSessionMessageTimelineItem]:
        for index, existing in enumerate(timeline):
            if existing.id == item.id:
                timeline[index] = item
                return timeline
        timeline.append(item)
        return timeline

    @staticmethod
    def _running_item_id(timeline: list[ChatSessionMessageTimelineItem], kind: str) -> str | None:
        for item in reversed(timeline):
            if item.kind == kind and item.status == "running":
                return item.id
        return None

    @staticmethod
    def _last_text_id(timeline: list[ChatSessionMessageTimelineItem]) -> str | None:
        if timeline and timeline[-1].kind == "text":
            return timeline[-1].id
        return None

    @staticmethod
    def _last_matching_tool_id(timeline: list[ChatSessionMessageTimelineItem], tool_name: object) -> str | None:
        for item in reversed(timeline):
            if item.kind == "tool" and (not tool_name or item.tool_name == tool_name):
                return item.id
        return None

    def _settle_running_timeline(
        self,
        timeline: list[ChatSessionMessageTimelineItem],
        kind: str,
        status: str,
        *,
        exclude_id: str | None = None,
    ) -> list[ChatSessionMessageTimelineItem]:
        return [
            item.model_copy(update={"status": status, "ended_at": item.ended_at or self._now_ms()})
            if item.kind == kind and item.status == "running" and item.id != exclude_id
            else item
            for item in timeline
        ]

    @staticmethod
    def _event_number(payload: dict[str, Any], key: str) -> int | float | None:
        value = payload.get(key)
        if isinstance(value, int | float):
            return value
        return None

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def _manager_response_message(self, event: ManagerWakeEvent, content: str, thinking: str | None) -> ChatSessionMessage:
        timeline = [
            ChatSessionMessageTimelineItem(
                id=f"wake_response_{event.wake_id}_text",
                kind="text",
                content=content,
                status="done",
            )
        ]
        if thinking:
            timeline.insert(
                0,
                ChatSessionMessageTimelineItem(
                    id=f"wake_response_{event.wake_id}_thinking",
                    kind="thinking",
                    content=thinking,
                    status="done",
                ),
            )
        return ChatSessionMessage(
            id=f"wake_response_{event.wake_id}",
            role="manager",
            content=content,
            thinking=thinking,
            state="done",
            timeline=timeline,
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
    def _wake_prompt(event: ManagerWakeEvent, directives: list[object]) -> str:
        directive_text = "\n".join(f"- {getattr(item, 'text', '')}" for item in directives if getattr(item, "text", None))
        lines = [
            "Auto mode wake event received.",
            f"kind: {event.kind}",
            f"message: {event.message}",
        ]
        if event.card_id:
            lines.append(f"card_id: {event.card_id}")
        if event.run_id:
            lines.append(f"run_id: {event.run_id}")
        if event.job_id:
            lines.append(f"job_id: {event.job_id}")
        if directive_text:
            lines.extend(["pending_directives:", directive_text])
        lines.append("Please inspect the relevant project state, decide the next safe action, and keep the project moving.")
        return "\n".join(lines)

    def _lock_for(self, project_id: str) -> Lock:
        if project_id not in self._project_locks:
            self._project_locks[project_id] = Lock()
        return self._project_locks[project_id]
