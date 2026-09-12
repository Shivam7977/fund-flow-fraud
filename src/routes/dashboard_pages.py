from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from auth.routes import get_current_user

router = APIRouter(tags=["dashboard-pages"])
templates = Jinja2Templates(directory="templates")


def _require_user(request: Request):
    """Returns the user, or None if not logged in (caller redirects)."""
    return get_current_user(request)


@router.get("/dashboard")
def dashboard_overview(request: Request):
    user = _require_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(
        request=request, name="dashboard.html",
        context={"active_tab": "overview", "user": user},
    )


@router.get("/dashboard/check")
def dashboard_check(request: Request):
    user = _require_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(
        request=request, name="dashboard_check.html",
        context={"active_tab": "check", "user": user},
    )


@router.get("/dashboard/uploads")
def dashboard_uploads(request: Request):
    user = _require_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(
        request=request, name="dashboard_uploads.html",
        context={"active_tab": "uploads", "user": user},
    )


@router.get("/dashboard/uploads/{job_id}")
def dashboard_job_detail(request: Request, job_id: str):
    user = _require_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(
        request=request, name="dashboard_job.html",
        context={"active_tab": "uploads", "user": user, "job_id": job_id},
    )