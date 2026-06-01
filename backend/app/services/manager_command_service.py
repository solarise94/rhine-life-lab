from __future__ import annotations

import json
import logging
from typing import Iterator
from uuid import uuid4

from fastapi import HTTPException

from app.models.chat import ChatRequest, ChatSessionMessage, ChatSessionMessageTimelineItem
from app.services.chat_session_service import ChatSessionService
from app.services.manager_auto_service import ManagerAutoService
from app.services.manager_wake_service import ManagerWakeService

logger = logging.getLogger(__name__)


def sse_event(event_type: str, **kwargs) -> bytes:
    data = {"type": event_type, **kwargs}
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


class ManagerCommandService:
    def __init__(
        self,
        manager_auto_service: ManagerAutoService,
        manager_wake_service: ManagerWakeService,
        chat_session_service: ChatSessionService,
    ) -> None:
        self.manager_auto_service = manager_auto_service
        self.manager_wake_service = manager_wake_service
        self.chat_session_service = chat_session_service

    def handle_auto_command_stream(
        self,
        project_id: str,
        request: ChatRequest,
        command_type: str,
        objective: str | None,
    ) -> Iterator[bytes]:
        user_message_id = request.message_id or f"cmd_usr_{uuid4().hex[:12]}"
        if user_message_id.startswith("cmd_usr_"):
            manager_message_id = user_message_id.replace("cmd_usr_", "cmd_mgr_", 1)
        else:
            manager_message_id = f"cmd_mgr_{uuid4().hex[:12]}"

        def persist_error_messages(user_msg: str, error_msg: str):
            try:
                user_message = ChatSessionMessage(
                    id=user_message_id,
                    role="user",
                    content=user_msg,
                    state="done",
                    timeline=[
                        ChatSessionMessageTimelineItem(
                            id=f"{user_message_id}_text",
                            kind="command",
                            content=user_msg,
                            status="done",
                        )
                    ],
                )
                manager_message = ChatSessionMessage(
                    id=manager_message_id,
                    role="manager",
                    content=error_msg,
                    state="error",
                    timeline=[
                        ChatSessionMessageTimelineItem(
                            id=f"{manager_message_id}_text",
                            kind="command",
                            content=error_msg,
                            status="error",
                        )
                    ],
                )
                self.chat_session_service.append_messages(
                    project_id,
                    request.session_id,
                    [user_message, manager_message],
                )
            except Exception as e:
                logger.exception("Failed to persist error messages: %s", e)

        try:
            if not request.session_id:
                err_msg = "session_id is required for /auto commands."
                yield sse_event("error", detail=err_msg)
                return
            try:
                self.chat_session_service.get_session(project_id, request.session_id)
            except ValueError as exc:
                err_msg = str(exc)
                yield sse_event("error", detail=err_msg)
                return

            ack_message_text = ""

            if command_type == "bare":
                ack_message_text = "请直接使用 `/auto <目标>`。例如：`/auto 继续推进 ready_to_start 的卡片，直到出现需要我决定的阻塞。`"
            elif command_type == "once":
                ack_message_text = "旧的 `/auto once` 输入入口已收起。请直接使用 `/auto <目标>`。"
            elif command_type == "status":
                state = self.manager_auto_service.get_state(project_id)
                if state.enabled:
                    ack_message_text = f"当前 auto 状态：active (enabled=True, owner={state.owner_session_id}, objective={state.scope_objective})"
                else:
                    ack_message_text = "当前 auto 状态：stopped (enabled=False)"
            elif command_type == "stop":
                current_state = self.manager_auto_service.get_state(project_id)
                if current_state.enabled and current_state.owner_session_id and current_state.owner_session_id != request.session_id:
                    err_msg = f"Only the auto owner session may stop auto mode. Current owner: {current_state.owner_session_id}"
                    persist_error_messages(request.message, err_msg)
                    yield sse_event("error", detail=err_msg)
                    return
                self.manager_auto_service.stop(
                    project_id,
                    request.session_id,
                    reason="user_stop",
                    message="因用户停止任务，已退出 auto 模式。",
                )
                ack_message_text = "因用户停止任务，已退出 auto 模式。"
            elif command_type == "enable":
                try:
                    state, directive, wake_event = self.manager_auto_service.enable_auto_flow(
                        project_id,
                        request.session_id,
                        self.chat_session_service,
                        self.manager_wake_service,
                        mode="continuous",
                        directive_text=objective,
                        message_id=user_message_id,
                        trigger_wake=True,
                    )
                    ack_message_text = f"已允许当前会话继续消费 workboard 并在后台唤醒。目标：{objective}"
                except HTTPException as exc:
                    persist_error_messages(request.message, exc.detail)
                    yield sse_event("error", detail=exc.detail)
                    return

            # Persist user & manager messages
            user_message = ChatSessionMessage(
                id=user_message_id,
                role="user",
                content=request.message,
                state="done",
                timeline=[
                    ChatSessionMessageTimelineItem(
                        id=f"{user_message_id}_text",
                        kind="command",
                        content=request.message,
                        status="done",
                    )
                ],
            )
            manager_message = ChatSessionMessage(
                id=manager_message_id,
                role="manager",
                content=ack_message_text,
                state="done",
                timeline=[
                    ChatSessionMessageTimelineItem(
                        id=f"{manager_message_id}_text",
                        kind="command",
                        content=ack_message_text,
                        status="done",
                    )
                ],
            )
            self.chat_session_service.append_messages(
                project_id,
                request.session_id,
                [user_message, manager_message],
            )

            yield sse_event("text_delta", delta=ack_message_text)
            yield sse_event(
                "response",
                response={
                    "message": ack_message_text,
                    "actions": [],
                    "warnings": [],
                },
            )
            yield sse_event("done")

        except Exception as exc:
            err_msg = str(exc)
            if request.session_id:
                persist_error_messages(request.message, err_msg)
            yield sse_event("error", detail=err_msg)
