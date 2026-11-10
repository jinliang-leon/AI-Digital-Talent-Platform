from pathlib import Path
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.controllers.deps import permission
from app.controllers.auth import user_view

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def page(request, user, key, title, nav, entity_id=""):
    return templates.TemplateResponse(
        request=request,
        name="workspace.html",
        context={"user": user_view(user), "page": key, "title": title, "nav": nav, "entity_id": entity_id},
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@router.get("/", response_class=HTMLResponse)
def overview(request: Request, user=Depends(permission("talents:read"))):
    return page(request, user, "overview", "工作台总览", "overview")


@router.get("/talents", response_class=HTMLResponse)
def talents(request: Request, user=Depends(permission("talents:read"))):
    return page(request, user, "talents", "人才库", "talents")


@router.get("/talents/{talent_id}", response_class=HTMLResponse)
def talent(request: Request, talent_id: str, user=Depends(permission("talents:read"))):
    return page(request, user, "talent_detail", "人才档案", "talents", talent_id)


@router.get("/jobs", response_class=HTMLResponse)
def jobs(request: Request, user=Depends(permission("jobs:read"))):
    return page(request, user, "jobs", "岗位管理", "jobs")


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job(request: Request, job_id: str, user=Depends(permission("jobs:read"))):
    return page(request, user, "job_detail", "岗位详情", "jobs", job_id)


@router.get("/search", response_class=HTMLResponse)
def search(request: Request, user=Depends(permission("search:read"))):
    return page(request, user, "search", "智能检索", "search")


@router.get("/tasks", response_class=HTMLResponse)
def tasks(request: Request, user=Depends(permission("talents:read"))):
    return page(request, user, "tasks", "处理任务", "tasks")


@router.get("/admin", response_class=HTMLResponse)
def admin(request: Request, user=Depends(permission("users:manage"))):
    return page(request, user, "admin", "系统管理", "admin")
