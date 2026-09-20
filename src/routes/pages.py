from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

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

# NOTE: /dashboard route yahan se hata diya gaya hai — dashboard_pages.py
# mein already ek /dashboard route tha (active_tab context ke saath, jo
# sidebar highlighting ke liye zaroori hai), dono ek saath register
# hone se ek silently unreachable ban jaata tha. Ab sirf ek hi hai.