"""应用工厂；存活探针不依赖数据库，所有服务延迟建立连接。"""

from contextlib import asynccontextmanager
from secrets import compare_digest
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from redis import Redis
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import make_session_factory
from app.errors import AppError
from app.middleware import UploadBodyLimit
from app.services.health import integration_status
from app.controllers import auth, admin, talents, jobs, search, web


def create_app(settings=None):
    settings = settings or get_settings()
    sessions, engine = make_session_factory(settings)
    cache = Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )

    @asynccontextmanager
    async def lifespan(app):
        yield
        cache.close()
        engine.dispose()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings, app.state.sessions, app.state.redis = settings, sessions, cache
    app.add_middleware(UploadBodyLimit, max_bytes=settings.max_upload_bytes + 65536)

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        request.state.request_id = uuid4().hex
        bearer = request.headers.get("authorization", "").lower().startswith("bearer ")
        cookie_auth = bool(request.cookies.get("access_token")) and not bearer
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and cookie_auth:
            cookie, header = request.cookies.get("csrf_token", ""), request.headers.get("x-csrf-token", "")
            if not cookie or not header or not compare_digest(cookie.encode(), header.encode()):
                return JSONResponse(
                    {
                        "code": "CSRF_FAILED",
                        "message": "页面验证已失效，请刷新后重试",
                        "request_id": request.state.request_id,
                    },
                    status_code=403,
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def business_error(request, exc):
        if not request.url.path.startswith(("/api/", "/health/")):
            if exc.status == 401:
                return RedirectResponse("/login", status_code=303, headers={"HX-Redirect": "/login"})
            if exc.status == 403:
                return HTMLResponse(
                    '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>无权访问</title><h1>当前账号没有此页面的访问权限</h1><a href="/">返回工作台</a></html>',
                    status_code=403,
                )
        return JSONResponse(
            {"code": exc.code, "message": exc.message, "request_id": request.state.request_id},
            status_code=exc.status,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {
                "code": "VALIDATION_ERROR",
                "message": "请求参数无效，请检查输入字段",
                "fields": [".".join(map(str, e["loc"])) for e in exc.errors()],
                "request_id": request.state.request_id,
            },
            status_code=422,
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return JSONResponse(
            {
                "code": "DATABASE_UNAVAILABLE",
                "message": "数据库操作暂不可用，请稍后重试",
                "request_id": request.state.request_id,
            },
            status_code=503,
        )

    @app.get("/health/live", tags=["健康检查"])
    def live():
        return {"status": "ok", "application": settings.app_name}

    @app.get("/health/ready", tags=["健康检查"])
    def ready():
        ok = all(item["ok"] for item in integration_status(settings, sessions, cache))
        return JSONResponse({"status": "ready" if ok else "not_ready"}, status_code=200 if ok else 503)

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(talents.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(search.router, prefix="/api/v1")
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
    app.include_router(web.router)
    return app
