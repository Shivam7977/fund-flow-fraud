from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from auth.routes import get_current_user

router = APIRouter(tags=["dashboard-pages"])
templates = Jinja2Templates(directory="templates")


def _require_user(request: Request):
    """Returns the user, or None if not logged in (caller redirects)."""
    return get_current_user(request)


def _no_cache_render(request: Request, name: str, context: dict | None = None):
    """
    Dashboard jaisa authenticated page render karke response pe
    Cache-Control: no-store lagata hai — taaki logout ke baad browser
    ka back button (bfcache) purana rendered page dikha na sake.
    Har protected page-rendering route isi ke through jaana chahiye,
    seedha templates.TemplateResponse() nahi.
    """
    response = templates.TemplateResponse(request=request, name=name, context=context or {})
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@router.get("/dashboard")
def dashboard_overview(request: Request):
    user = _require_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return _no_cache_render(
        request, "dashboard.html",
        context={"active_tab": "overview", "user": user},
    )


@router.get("/dashboard/check")
def dashboard_check(request: Request):
    user = _require_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return _no_cache_render(
        request, "dashboard_check.html",
        context={"active_tab": "check", "user": user},
    )


@router.get("/dashboard/uploads")
def dashboard_uploads(request: Request):
    user = _require_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return _no_cache_render(
        request, "dashboard_uploads.html",
        context={"active_tab": "uploads", "user": user},
    )


@router.get("/dashboard/uploads/{job_id}")
def dashboard_job_detail(request: Request, job_id: str):
    user = _require_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return _no_cache_render(
        request, "dashboard_job.html",
        context={"active_tab": "uploads", "user": user, "job_id": job_id},
    )


@router.get("/dashboard/history")
def dashboard_history(request: Request):
    user = _require_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return _no_cache_render(
        request, "dashboard_history.html",
        context={"active_tab": "history", "user": user},
    )