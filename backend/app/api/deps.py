from functools import lru_cache

from app.services.chat_session_service import ChatSessionService
from app.services.app_config_service import AppConfigService
from app.services.manager_service import ManagerService
from app.services.chat_job_service import ChatJobService
from app.services.flow_service import FlowService
from app.services.manifest_service import ManifestService
from app.services.patch_apply import PatchApplyService
from app.services.patch_validator import PatchValidator
from app.services.project_file_service import ProjectFileService
from app.services.project_service import ProjectService
from app.services.result_asset_service import ResultAssetService
from app.services.report_service import ReportService
from app.services.runtime_dependency_job_service import RuntimeDependencyJobService
from app.services.runtime_approval_service import RuntimeApprovalService
from app.services.worker_service import WorkerService


@lru_cache
def get_project_service() -> ProjectService:
    service = ProjectService()
    service.ensure_seed_project()
    return service


@lru_cache
def get_patch_validator() -> PatchValidator:
    return PatchValidator(get_project_service())


@lru_cache
def get_patch_apply_service() -> PatchApplyService:
    return PatchApplyService(get_project_service(), get_patch_validator())


@lru_cache
def get_manager_service() -> ManagerService:
    return ManagerService(
        get_project_service(),
        worker_service=get_worker_service(),
        runtime_dependency_job_service=get_runtime_dependency_job_service(),
    )


@lru_cache
def get_chat_job_service() -> ChatJobService:
    return ChatJobService()


@lru_cache
def get_runtime_dependency_job_service() -> RuntimeDependencyJobService:
    return RuntimeDependencyJobService()


@lru_cache
def get_chat_session_service() -> ChatSessionService:
    return ChatSessionService(get_project_service())


@lru_cache
def get_runtime_approval_service() -> RuntimeApprovalService:
    return RuntimeApprovalService(get_project_service())


@lru_cache
def get_worker_service() -> WorkerService:
    return WorkerService(get_project_service(), get_manifest_service(), get_runtime_approval_service())


@lru_cache
def get_manifest_service() -> ManifestService:
    return ManifestService(get_project_service())


@lru_cache
def get_report_service() -> ReportService:
    return ReportService(get_project_service())


@lru_cache
def get_result_asset_service() -> ResultAssetService:
    return ResultAssetService(get_project_service())


@lru_cache
def get_project_file_service() -> ProjectFileService:
    return ProjectFileService(get_project_service())


@lru_cache
def get_flow_service() -> FlowService:
    return FlowService(get_project_service())


@lru_cache
def get_app_config_service() -> AppConfigService:
    return AppConfigService()
