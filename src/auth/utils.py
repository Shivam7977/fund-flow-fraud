import bcrypt
import random
import secrets
from datetime import datetime, timezone


# ---------- Password hashing ----------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# ---------- OTP ----------

def generate_otp() -> str:
    return str(random.randint(100000, 999999))


def is_otp_expired(otp_created_at: str, max_age_minutes: int = 10) -> bool:
    created = datetime.fromisoformat(otp_created_at)
    now = datetime.now(timezone.utc)
    elapsed_minutes = (now - created).total_seconds() / 60
    return elapsed_minutes > max_age_minutes


# ---------- Session tokens ----------

def generate_session_token() -> str:
    """
    Cryptographically secure random token — is user ki session cookie
    ki value banegi. Guess karna practically impossible hai.
    """
    return secrets.token_urlsafe(32)