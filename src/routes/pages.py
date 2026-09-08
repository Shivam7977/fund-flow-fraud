from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from auth.routes import get_current_user

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="templates")


@router.get("/")
def home(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard")
    return RedirectResponse(url="/login")


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