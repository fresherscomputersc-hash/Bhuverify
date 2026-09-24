"""
BhuVerify - FastAPI application entry point.

Serves the REST API (SRS FR-17) and the single-page reviewer/manager console.
On first boot it creates the schema, seeds the demo dataset described in SRS
section 10, and starts the background processing worker.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import security
from app.config import STATIC_DIR, settings
from app.database import init_db
from app.routers import audit as audit_router
from app.routers import auth as auth_router
from app.routers import dashboard as dashboard_router
from app.routers import documents as documents_router
from app.routers import map as map_router
from app.routers import records as records_router
from app.routers import system as system_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.seed import ensure_seeded

    report = ensure_seeded()
    app.state.seed_report = report

    from app.services.worker import ensure_worker

    ensure_worker()
    yield


app = FastAPI(
    title=settings.app_name,
    description=(
        f"{settings.app_subtitle} - {settings.problem_statement} ({settings.theme}). "
        "AI-assisted land record digitization and validation with human-in-the-loop approval."
    ),
    version=settings.version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (
    auth_router.router,
    documents_router.router,
    records_router.router,
    audit_router.router,
    map_router.router,
    dashboard_router.router,
    system_router.router,
):
    app.include_router(router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/api/v1/health", tags=["system"])
def health():
    from app.services.ocr_service import TESSERACT_AVAILABLE, TESSERACT_VERSION

    return {
        "status": "ok",
        "app": settings.app_name,
        "problem_statement": settings.problem_statement,
        "version": settings.version,
        "ocr": {"tesseract": TESSERACT_AVAILABLE, "version": TESSERACT_VERSION},
    }


@app.get("/health", include_in_schema=False)
def health_alias():
    """Plain /health alias (container healthchecks, browser extensions)."""
    return health()


@app.get("/", include_in_schema=False)
def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse({"detail": "Frontend not built. API docs are at /docs."})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return JSONResponse({}, status_code=204)
