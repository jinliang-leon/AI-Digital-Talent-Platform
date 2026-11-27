from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from starlette.exceptions import HTTPException
from fastapi.exceptions import RequestValidationError

from .core.config import ROOT, Settings
from .core.security import Actor, DB, require_role
from .core.storage import LocalStorage, MinioStorage
from .db import make_engine
from .models import Application, Candidate, Job, ResumeFile
from .modules import applications, candidates, chat, files, identity, interview_ai, jobs, knowledge, resumes, talent_search

logger = logging.getLogger("talent_hub")


def create_app(settings=None):
    @asynccontextmanager
    async def lifespan(app):
        config = settings or Settings.load()
        if config.profile == "local":
            (ROOT / ".runtime").mkdir(exist_ok=True)
        app.state.settings = config
        app.state.fernet = Fernet(config.contact_key.encode())
        app.state.engine = make_engine(config.database_url)
        app.state.storage = LocalStorage(config.storage_path) if config.profile == "local" else MinioStorage(config)
        if config.profile == "compose":
            from redis import Redis
            app.state.redis = Redis.from_url(config.redis_url, socket_connect_timeout=2, socket_timeout=2)
        try:
            yield
        finally:
            if config.profile == "compose":
                app.state.redis.close()
            app.state.engine.dispose()

    app = FastAPI(title="AI 数字化人才平台", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.trace_id = uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.trace_id
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"code": f"HTTP_{exc.status_code}", "message": str(exc.detail),
                             "trace_id": request.state.trace_id}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        errors = [{"field": ".".join(map(str, item["loc"])), "type": item["type"]} for item in exc.errors()]
        return JSONResponse({"code": "INVALID_INPUT", "message": "表单字段格式有误", "field_errors": errors,
                             "trace_id": request.state.trace_id}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.error("Unexpected API error trace_id=%s type=%s", request.state.trace_id, type(exc).__name__)
        return JSONResponse({"code": "INTERNAL_ERROR", "message": "服务处理失败",
                             "trace_id": request.state.trace_id}, status_code=500)

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready(request: Request):
        state = request.app.state
        def probe(name, operation):
            try:
                return "ok" if operation() else "error"
            except Exception as exc:
                logger.warning("readiness %s failed: %s", name, type(exc).__name__)
                return "error"

        def db_query(sql):
            with state.engine.connect() as connection:
                return connection.scalar(text(sql))

        def migration_matches():
            from .core.migrations import config_for
            from alembic.script import ScriptDirectory
            head = ScriptDirectory.from_config(config_for()).get_current_head()
            return db_query("SELECT version_num FROM alembic_version") == head

        checks = {"database": probe("database", lambda: db_query("SELECT 1")),
                  "migration": probe("migration", migration_matches), "pgvector": "not_configured",
                  "redis": "not_configured", "minio": "not_configured"}
        if state.settings.profile == "compose":
            checks["pgvector"] = probe("pgvector", lambda: db_query("SELECT '[1,0]'::vector::text"))
            checks["redis"] = probe("redis", state.redis.ping)
            checks["minio"] = probe("minio", state.storage.check)
        else:
            checks["local_storage"] = probe("local_storage", state.storage.check)
        healthy = "error" not in checks.values()
        result = {"status": ("ok" if state.settings.profile == "compose" else "local_only") if healthy else "error",
                  "profile": state.settings.profile, "checks": checks}
        return JSONResponse(result, status_code=200 if healthy else 503)

    api = APIRouter(prefix="/api/v1")
    for router in (identity.router, jobs.router, candidates.router, applications.router, files.router,
                   resumes.router, talent_search.router, knowledge.router, interview_ai.router, chat.router):
        api.include_router(router)

    @api.get("/dashboard")
    def dashboard(db: DB, actor: Actor):
        require_role(actor, "hr")
        china = timezone(timedelta(hours=8))
        today = datetime.now(china).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        counts = db.execute(select(Application.status, func.count()).group_by(Application.status)).all()
        return {"open_jobs": db.scalar(select(func.count()).select_from(Job).where(Job.status == "open")),
                "candidates": db.scalar(select(func.count()).select_from(Candidate)),
                "files_today": db.scalar(select(func.count()).select_from(ResumeFile).where(ResumeFile.created_at >= today)),
                "pending_review": dict(counts).get("pending_review", 0),
                "pending_interview": dict(counts).get("pending_interview", 0),
                "applications_by_status": dict(counts)}

    app.include_router(api)
    return app


app = create_app()
