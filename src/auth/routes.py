from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Response, HTTPException
from config import settings
from auth.models import SignupRequest, OTPVerifyRequest, LoginRequest, MessageResponse
from auth.utils import hash_password, verify_password, generate_otp, is_otp_expired, generate_session_token
from auth.email_service import send_otp_email
from auth.google_oauth import get_google_redirect_url, handle_google_callback
from core.db import (
    create_pending_signup, get_pending_signup, delete_pending_signup,
    create_user, get_user_by_email, create_session, get_session,
    delete_session, get_user_by_id,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, user_id: int):
    session_id = generate_session_token()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=settings.SESSION_MAX_AGE)).isoformat()
    create_session(session_id, user_id, expires_at)
    response.set_cookie(
        settings.SESSION_COOKIE_NAME,
        session_id,
        max_age=settings.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


# ---------- Signup + OTP ----------

@router.post("/signup", response_model=MessageResponse)
def signup(data: SignupRequest):
    if get_user_by_email(data.email):
        raise HTTPException(400, "Yeh email already registered hai")

    password_hash = hash_password(data.password)
    otp = generate_otp()
    create_pending_signup(data.email, data.name, data.username, password_hash, otp)

    sent = send_otp_email(data.email, otp)
    if not sent:
        raise HTTPException(500, "OTP email bhejne mein dikkat aayi, dobara try karo")

    return MessageResponse(status="ok", message="OTP bhej diya gaya hai, email check karo")


@router.post("/verify-otp", response_model=MessageResponse)
def verify_otp(data: OTPVerifyRequest):
    pending = get_pending_signup(data.email)
    if not pending:
        raise HTTPException(400, "Koi pending signup nahi mila is email ke liye")

    if is_otp_expired(pending["otp_created_at"]):
        delete_pending_signup(data.email)
        raise HTTPException(400, "OTP expire ho chuka hai, dobara signup karo")

    if pending["otp"] != data.otp:
        raise HTTPException(400, "Galat OTP")

    create_user(
        name=pending["name"], email=data.email, username=pending["username"],
        password_hash=pending["password_hash"], auth_provider="password", is_verified=1,
    )
    delete_pending_signup(data.email)

    return MessageResponse(status="ok", message="Account ban gaya, ab login karo")


# ---------- Login / Logout ----------

@router.post("/login", response_model=MessageResponse)
def login(data: LoginRequest, response: Response):
    user = get_user_by_email(data.email)
    if not user or not user["password_hash"]:
        raise HTTPException(401, "Email ya password galat hai")

    if not verify_password(data.password, user["password_hash"]):
        raise HTTPException(401, "Email ya password galat hai")

    _set_session_cookie(response, user["id"])
    return MessageResponse(status="ok", message="Login successful")


@router.post("/logout", response_model=MessageResponse)
def logout(request: Request, response: Response):
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if session_id and session_id != "guest":
        delete_session(session_id)
    response.delete_cookie(settings.SESSION_COOKIE_NAME)
    return MessageResponse(status="ok", message="Logout ho gaya")


# ---------- Guest ----------

@router.post("/guest-login", response_model=MessageResponse)
def guest_login(response: Response):
    response.set_cookie(
        settings.SESSION_COOKIE_NAME,
        "guest",
        httponly=True,
        samesite="lax",
    )
    return MessageResponse(status="ok", message="Guest mode active")


# ---------- Google OAuth ----------

@router.get("/google/login")
async def google_login(request: Request):
    return await get_google_redirect_url(request)


@router.get("/google/callback")
async def google_callback(request: Request, response: Response):
    try:
        user = await handle_google_callback(request)
    except Exception as e:
        raise HTTPException(400, f"Google login fail hua: {e}")

    _set_session_cookie(response, user["id"])
    return {"status": "ok", "message": "Google login successful", "user": user["email"]}


# ---------- Current user helper (dusre routes ke liye) ----------

def get_current_user(request: Request):
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not session_id:
        return None
    if session_id == "guest":
        return {"guest": True}

    session = get_session(session_id)
    if not session:
        return None

    if datetime.fromisoformat(session["expires_at"]) < datetime.now(timezone.utc):
        delete_session(session_id)
        return None

    return get_user_by_id(session["user_id"])