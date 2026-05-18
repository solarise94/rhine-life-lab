from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import advanced, chat, manager_tools, projects, report, results, runs
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix=settings.api_prefix)
app.include_router(chat.router, prefix=settings.api_prefix)
app.include_router(manager_tools.router, prefix=settings.api_prefix)
app.include_router(results.router, prefix=settings.api_prefix)
app.include_router(report.router, prefix=settings.api_prefix)
app.include_router(runs.router, prefix=settings.api_prefix)
app.include_router(advanced.router, prefix=settings.api_prefix)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
