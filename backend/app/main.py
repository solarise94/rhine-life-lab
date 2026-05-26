from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import advanced, app_settings, chat, chat_sessions, diagnostics, files, library, manager_auto, manager_tools, projects, report, results, runs
from app.api.deps import get_app_config_service, get_manager_wake_processor, get_worker_service
from app.core.config import get_settings

settings = get_settings()


def initialize_runtime_services() -> None:
    get_app_config_service()
    get_worker_service()
    get_manager_wake_processor().start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_runtime_services()
    try:
        yield
    finally:
        get_manager_wake_processor().stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix=settings.api_prefix)
app.include_router(app_settings.router, prefix=settings.api_prefix)
app.include_router(library.router, prefix=settings.api_prefix)
app.include_router(chat.router, prefix=settings.api_prefix)
app.include_router(manager_auto.router, prefix=settings.api_prefix)
app.include_router(manager_tools.router, prefix=settings.api_prefix)
app.include_router(diagnostics.router, prefix=settings.api_prefix)
app.include_router(results.router, prefix=settings.api_prefix)
app.include_router(report.router, prefix=settings.api_prefix)
app.include_router(runs.router, prefix=settings.api_prefix)
app.include_router(advanced.router, prefix=settings.api_prefix)
app.include_router(files.router, prefix=settings.api_prefix)
app.include_router(chat_sessions.router, prefix=settings.api_prefix)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
