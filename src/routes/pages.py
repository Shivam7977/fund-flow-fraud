from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from auth.routes import get_current_user

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="templates")


@router.get("/")
def home(request: Request):
    return templates.TemplateResponse(request=request, name="landing.html")


@router.get("/signup")
def signup_page(request: Request):
    # NOTE: new-style call (request first) — old style
    # TemplateResponse("name", {"request": request}) triggers a
    # Jinja2/Starlette cache-key bug (TypeError: unhashable type: 'dict')
    # on some version combinations.
    return templates.TemplateResponse(request=request, name="signup.html")


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@router.get("/forgot-password")
def forgot_password_page(request: Request):
    return templates.TemplateResponse(request=request, name="forgot_password.html")


@router.get("/reset-password")
def reset_password_page(request: Request):
    # Token query param JS se URLSearchParams se padha jaata hai
    # (window.location.search), isliye yahan context mein pass
    # karne ki zaroorat nahi.
    return templates.TemplateResponse(request=request, name="reset_password.html")


@router.get("/dashboard")
def dashboard_page(request: Request):
    """
    NOTE: dashboard_pages.py mein bhi ek /dashboard route hai — dono
    routers app.py mein include hote hain to jo pehle include hota hai
    wahi actually serve karega, ye doosra silently unreachable ho jaata
    hai. Dono mein no-store header laga diya hai taaki jo bhi active ho,
    logout ke baad bfcache se stale dashboard na dikhe.
    """
    user = get_current_user(request)

    if user is None:
        return RedirectResponse(url="/login", status_code=303)

    is_guest = user.get("guest") is True

    response = templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user,
            "is_guest": is_guest,
        },
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response