from functools import lru_cache

from app.services.chat_session_service import ChatSessionService
from app.services.app_config_service import AppConfigService
from app.services.background_task_service import BackgroundTaskService
from app.services.background_workboard_service import BackgroundWorkboardService
from app.services.diagnostic_bundle_service import DiagnosticBundleService
from app.services.manager_service import ManagerService
from app.services.chat_job_service import ChatJobService
from app.services.flow_service import FlowService
from app.services.library_registry_service import LibraryRegistryService
from app.services.manager_auto_service import ManagerAutoService
from app.services.manifest_service import ManifestService
from app.services.manager_wake_processor import ManagerWakeProcessor
from app.services.manager_wake_service import ManagerWakeService
from app.services.patch_apply import PatchApplyService
from app.services.patch_validator import PatchValidator
from app.services.project_file_service import ProjectFileService
from app.services.project_event_service import ProjectEventService
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
        library_registry_service=get_library_registry_service(),
        manager_auto_service=get_manager_auto_service(),
        background_workboard_service=get_background_workboard_service(),
    )


@lru_cache
def get_chat_job_service() -> ChatJobService:
    return ChatJobService()


@lru_cache
def get_runtime_dependency_job_service() -> RuntimeDependencyJobService:
    return RuntimeDependencyJobService(
        get_project_service(),
        project_event_service=get_project_event_service(),
        background_task_service=get_background_task_service(),
        background_terminal_callback=lambda project_id, run_id=None, job_id=None: get_manager_auto_service().notify_background_task_terminal(
            project_id,
            run_id=run_id,
            job_id=job_id,
        ),
    )


@lru_cache
def get_chat_session_service() -> ChatSessionService:
    return ChatSessionService(get_project_service(), get_manager_auto_service())


@lru_cache
def get_project_event_service() -> ProjectEventService:
    return ProjectEventService(get_project_service())


@lru_cache
def get_runtime_approval_service() -> RuntimeApprovalService:
    return RuntimeApprovalService(get_project_service())


@lru_cache
def get_worker_service() -> WorkerService:
    return WorkerService(
        get_project_service(),
        get_manifest_service(),
        get_runtime_approval_service(),
        get_library_registry_service(),
        project_event_service=get_project_event_service(),
        background_task_service=get_background_task_service(),
        background_terminal_callback=lambda project_id, run_id=None, job_id=None: get_manager_auto_service().notify_background_task_terminal(
            project_id,
            run_id=run_id,
            job_id=job_id,
        ),
    )


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


@lru_cache
def get_library_registry_service() -> LibraryRegistryService:
    return LibraryRegistryService(
        get_project_service(),
        get_app_config_service(),
    )


@lru_cache
def get_manager_auto_service() -> ManagerAutoService:
    return ManagerAutoService(
        get_project_service(),
        get_project_event_service(),
        get_background_workboard_service(),
        get_manager_wake_service(),
    )


@lru_cache
def get_background_task_service() -> BackgroundTaskService:
    return BackgroundTaskService(get_project_service())


@lru_cache
def get_background_workboard_service() -> BackgroundWorkboardService:
    return BackgroundWorkboardService(get_project_service(), get_background_task_service())


@lru_cache
def get_manager_wake_service() -> ManagerWakeService:
    return ManagerWakeService(get_project_service())


@lru_cache
def get_manager_wake_processor() -> ManagerWakeProcessor:
    return ManagerWakeProcessor(
        get_project_service(),
        get_manager_auto_service(),
        get_manager_wake_service(),
        get_chat_session_service(),
        get_manager_service(),
    )


@lru_cache
def get_diagnostic_bundle_service() -> DiagnosticBundleService:
    return DiagnosticBundleService(
        get_project_service(),
        get_app_config_service(),
    )
