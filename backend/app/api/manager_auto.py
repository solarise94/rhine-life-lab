from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_chat_session_service, get_manager_auto_service, get_manager_wake_service
from app.models.manager_auto import ManagerAutoState
from app.services.chat_session_service import ChatSessionService
from app.services.manager_auto_service import ManagerAutoService
from app.services.manager_wake_service import ManagerWakeService
from app.services.worker_service import WorkerService

router = APIRouter(prefix="/projects/{project_id}/manager-auto", tags=["manager-auto"])


class SetManagerAutoRequest(BaseModel):
    session_id: str
    mode: str = "continuous"
    directive_text: str | None = None
    message_id: str | None = None
    trigger_wake: bool = True


class StopManagerAutoRequest(BaseModel):
    session_id: str
    reason: str = "user_off"
    message: str = "Auto mode 已关闭。"


class AddManagerAutoDirectiveRequest(BaseModel):
    session_id: str
    text: str
    message_id: str | None = None
    trigger_wake: bool = True


class SettleManagerAutoTurnRequest(BaseModel):
    session_id: str
    async_boundary: bool = False


def _status_payload(state: ManagerAutoState) -> dict:
    return {
        "state": state.model_dump(),
    }


@router.get("")
def get_manager_auto_status(
    project_id: str,
    session_id: str | None = None,
    manager_auto_service: ManagerAutoService = Depends(get_manager_auto_service),
) -> dict:
    view = manager_auto_service.get_view(project_id, session_id)
    return {
        **_status_payload(view.state),
        "is_owner": view.is_owner,
        "btw_mode": view.btw_mode,
    }


@router.post("")
def enable_manager_auto(
    project_id: str,
    request: SetManagerAutoRequest,
    chat_session_service: ChatSessionService = Depends(get_chat_session_service),
    manager_auto_service: ManagerAutoService = Depends(get_manager_auto_service),
    manager_wake_service: ManagerWakeService = Depends(get_manager_wake_service),
) -> dict:
    state, directive, wake_event = manager_auto_service.enable_auto_flow(
        project_id,
        request.session_id,
        chat_session_service,
        manager_wake_service,
        mode=request.mode,
        directive_text=request.directive_text,
        message_id=request.message_id,
        trigger_wake=request.trigger_wake,
    )
    return {
        **_status_payload(state),
        "directive": directive.model_dump() if directive else None,
        "wake_event": wake_event.model_dump() if wake_event else None,
    }


@router.post("/stop")
def stop_manager_auto(
    project_id: str,
    request: StopManagerAutoRequest,
    manager_auto_service: ManagerAutoService = Depends(get_manager_auto_service),
) -> dict:
    state = manager_auto_service.stop(
        project_id,
        request.session_id,
        reason=request.reason,
        message=request.message,
    )
    return _status_payload(state)


@router.post("/directives")
def add_manager_auto_directive(
    project_id: str,
    request: AddManagerAutoDirectiveRequest,
    manager_auto_service: ManagerAutoService = Depends(get_manager_auto_service),
    manager_wake_service: ManagerWakeService = Depends(get_manager_wake_service),
) -> dict:
    directive = manager_auto_service.add_directive(
        project_id,
        request.session_id,
        text=request.text,
        message_id=request.message_id,
    )
    wake_event = None
    if request.trigger_wake:
        from app.models.manager_auto import ManagerWakeEvent
        from app.services.utils import utc_now

        if manager_auto_service.should_trigger_directive_wake(project_id):
            wake_event = ManagerWakeEvent(
                wake_id=f"wake_{directive.id}",
                project_id=project_id,
                kind="directive_received",
                source_type="directive",
                source_id=directive.id,
                severity="info",
                message=f"收到新的 auto 指令：{directive.text}",
                payload_summary={"directive_id": directive.id},
                idempotency_key=f"directive:{directive.id}",
                created_at=utc_now(),
            )
            manager_wake_service.enqueue(wake_event)
    return {
        "directive": directive.model_dump(),
        "wake_event": wake_event.model_dump() if wake_event else None,
        "state": manager_auto_service.get_state(project_id).model_dump(),
    }


@router.get("/wake-events")
def list_manager_wake_events(
    project_id: str,
    manager_wake_service: ManagerWakeService = Depends(get_manager_wake_service),
) -> dict:
    return {
        "items": [item.model_dump() for item in manager_wake_service.list_recent(project_id)],
    }


@router.post("/turn-settled")
def settle_manager_auto_turn(
    project_id: str,
    request: SettleManagerAutoTurnRequest,
    manager_auto_service: ManagerAutoService = Depends(get_manager_auto_service),
) -> dict:
    state = manager_auto_service.notify_turn_settled(
        project_id,
        request.session_id,
        async_boundary=request.async_boundary,
    )
    return _status_payload(state)
