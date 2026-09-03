import resend
from config import settings

resend.api_key = settings.RESEND_API_KEY


def send_otp_email(to_email: str, otp: str) -> bool:
    """
    OTP email bhejta hai. Success pe True, fail pe False return karta hai
    (exception raise nahi karta — signup flow ko crash nahi karna).
    """
    try:
        resend.Emails.send({
            "from": settings.EMAIL_FROM,
            "to": [to_email],
            "subject": "Fund Flow — Verify your email",
            "html": f"""
                <div style="font-family: sans-serif; max-width: 480px; margin: auto;">
                    <h2>Verify your email</h2>
                    <p>Your OTP for Fund Flow Fraud Detection signup is:</p>
                    <h1 style="letter-spacing: 4px;">{otp}</h1>
                    <p>This OTP is valid for 10 minutes. If you didn't request this, ignore this email.</p>
                </div>
            """,
        })
        return True
    except Exception as e:
        print(f"[email_service] Failed to send OTP email: {e}")
        return False


def send_password_reset_email(to_email: str, reset_link: str) -> bool:
    try:
        resend.Emails.send({
            "from": settings.EMAIL_FROM,
            "to": [to_email],
            "subject": "Fund Flow — Reset your password",
            "html": f"""
                <div style="font-family: sans-serif; max-width: 480px; margin: auto;">
                    <h2>Reset your password</h2>
                    <p>Click the link below to reset your password. This link is valid for 1 hour.</p>
                    <a href="{reset_link}">{reset_link}</a>
                </div>
            """,
        })
        return True
    except Exception as e:
        print(f"[email_service] Failed to send reset email: {e}")
        return False