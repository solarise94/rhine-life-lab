import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import get_project_event_service
from app.services.project_event_service import ProjectEventService


# Mounted under settings.api_prefix in app.main, so the public path is
# /api/projects/{project_id}/events by default.
router = APIRouter(prefix="/projects/{project_id}/events", tags=["project-events"])


@router.get("")
def stream_project_events(
    project_id: str,
    project_event_service: ProjectEventService = Depends(get_project_event_service),
) -> StreamingResponse:
    def iterator():
        for event in project_event_service.subscribe_events(project_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")

    return StreamingResponse(
        iterator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
