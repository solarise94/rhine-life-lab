from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.deps import get_project_file_service
from app.services.project_file_service import ProjectFileService

router = APIRouter(prefix="/projects/{project_id}", tags=["files"])


@router.get("/files")
def get_project_files(project_id: str, project_file_service: ProjectFileService = Depends(get_project_file_service)) -> dict:
    return project_file_service.list_files(project_id)


@router.delete("/files/session-uploads/{asset_id}")
def delete_session_upload(
    project_id: str,
    asset_id: str,
    project_file_service: ProjectFileService = Depends(get_project_file_service),
) -> dict:
    try:
        asset = project_file_service.delete_session_upload(project_id, asset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Session upload not found: {asset_id}") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Asset is not a session upload: {asset_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "asset": asset}


@router.delete("/files/assets/{asset_id}")
def delete_data_asset(
    project_id: str,
    asset_id: str,
    project_file_service: ProjectFileService = Depends(get_project_file_service),
) -> dict:
    try:
        asset = project_file_service.delete_data_asset(project_id, asset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "asset": asset}


@router.get("/files/content")
def get_project_file_content(
    project_id: str,
    path: str = Query(...),
    project_file_service: ProjectFileService = Depends(get_project_file_service),
) -> FileResponse:
    try:
        return project_file_service.get_execution_file_response(project_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"File not found: {path}") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Path is not an execution file: {path}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
