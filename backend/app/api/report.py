from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import get_report_service
from app.services.report_service import ReportService

router = APIRouter(prefix="/projects/{project_id}", tags=["report"])


class ReorderReportRequest(BaseModel):
    item_ids: list[str] = Field(default_factory=list)


@router.get("/report")
def get_report(project_id: str, report_service: ReportService = Depends(get_report_service)) -> dict:
    return report_service.build_report(project_id)


@router.post("/report/reorder")
def reorder_report(
    project_id: str,
    request: ReorderReportRequest,
    report_service: ReportService = Depends(get_report_service),
) -> dict:
    return report_service.reorder_sections(project_id, request.item_ids)


@router.post("/report/export-html")
def export_report_html(project_id: str, report_service: ReportService = Depends(get_report_service)) -> dict:
    return report_service.export_html(project_id)


@router.get("/report/exported-html")
def get_exported_report_html(project_id: str, report_service: ReportService = Depends(get_report_service)) -> FileResponse:
    report = report_service.get_exported_html(project_id)
    path = report_service.project_service.project_path(project_id) / report["path"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report has not been exported yet.")
    return FileResponse(path, media_type="text/html")
