from authlib.integrations.starlette_client import OAuth
from config import settings
from core.db import get_user_by_email, create_user, update_user_auth_provider

oauth = OAuth()

oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


async def get_google_redirect_url(request):
    """
    Google consent screen pe bhejne wala URL banata hai.
    /auth/google/login route isko call karega.
    """
    return await oauth.google.authorize_redirect(request, settings.GOOGLE_REDIRECT_URI)


async def handle_google_callback(request) -> dict:
    """
    Google se wapas aane ke baad (/auth/google/callback) call hoga.
    Token exchange karta hai, user info nikaalta hai, aur DB mein
    account create/merge karta hai. Return: user dict.
    """
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo")

    if not userinfo or not userinfo.get("email"):
        raise ValueError("Google se email nahi mila")

    email = userinfo["email"]
    name = userinfo.get("name", email.split("@")[0])

    existing = get_user_by_email(email)

    if existing is None:
        # Naya user — Google se aaya hai, isliye OTP verify ki zaroorat nahi
        # (Google already email verify kar chuka hai)
        create_user(
            name=name,
            email=email,
            username=None,
            password_hash=None,
            auth_provider="google",
            is_verified=1,
        )
        user = get_user_by_email(email)
    else:
        # Existing user (password se signup kiya tha) — ab Google se bhi login kar raha
        if existing["auth_provider"] == "password":
            update_user_auth_provider(email, "both")
        user = get_user_by_email(email)

    return user