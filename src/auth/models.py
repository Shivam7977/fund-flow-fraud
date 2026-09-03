import re
from pydantic import BaseModel, EmailStr, field_validator


class SignupRequest(BaseModel):
    name: str
    email: EmailStr
    username: str
    password: str
    confirm_password: str

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password kam se kam 8 characters ka hona chahiye")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password mein kam se kam 1 lowercase letter hona chahiye")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password mein kam se kam 1 uppercase letter hona chahiye")
        if not re.search(r"[0-9]", v):
            raise ValueError("Password mein kam se kam 1 number hona chahiye")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>_\-+=~`\[\];']", v):
            raise ValueError("Password mein kam se kam 1 special character hona chahiye")
        return v

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("Password aur Confirm Password match nahi karte")
        return v


class OTPVerifyRequest(BaseModel):
    email: EmailStr
    otp: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    username: str | None
    auth_provider: str
    is_verified: bool


class MessageResponse(BaseModel):
    status: str
    message: str