from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.deps import get_diagnostic_bundle_service
from app.services.diagnostic_bundle_service import DiagnosticBundleService
from app.services.utils import resolve_within

router = APIRouter(prefix="/projects/{project_id}", tags=["diagnostics"])


@router.post("/diagnostics/export")
def export_diagnostics(
    project_id: str,
    max_runs: int = Query(default=8, ge=1, le=20),
    diagnostic_bundle_service: DiagnosticBundleService = Depends(get_diagnostic_bundle_service),
) -> dict:
    return diagnostic_bundle_service.build_bundle(project_id, max_runs=max_runs)


@router.get("/diagnostics/download")
def download_diagnostics(
    project_id: str,
    path: str,
    diagnostic_bundle_service: DiagnosticBundleService = Depends(get_diagnostic_bundle_service),
) -> FileResponse:
    project_root = diagnostic_bundle_service.project_service.project_path(project_id)
    candidate = resolve_within(project_root, path)
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Diagnostic bundle not found.")
    diagnostics_root = (project_root / "reports" / "diagnostics").resolve()
    if candidate.resolve() != diagnostics_root and diagnostics_root not in candidate.resolve().parents:
        raise HTTPException(status_code=403, detail="File is outside diagnostics directory.")
    return FileResponse(candidate, media_type="application/zip", filename=candidate.name)
