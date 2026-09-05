import os
from pathlib import Path
from dotenv import load_dotenv

# Project root (fund-flow-fraud/) dhoondo — chahe app.py kahin se bhi run ho
BASE_DIR = Path(__file__).resolve().parent.parent

# .env file root se load karo
load_dotenv(BASE_DIR / ".env")


class Settings:
    # App
    SECRET_KEY: str = os.getenv("SECRET_KEY", "insecure-default-change-me")

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # Static baseline data
    STATIC_CSV_PATH: str = str(BASE_DIR / "data" / "fraud_graph_data.csv")
    ACCOUNT_TYPE_MAP_PATH: str = str(BASE_DIR / "data" / "account_type_map.json")

    # Models
    MODELS_DIR: str = str(BASE_DIR / "models")

    # Resend
    RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
    EMAIL_FROM: str = "Fund Flow Fraud Detection <fundflow@skillbridge-ai.tech>"

    # Google OAuth
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")

    # Session
    SESSION_COOKIE_NAME: str = "session_id"
    SESSION_MAX_AGE: int = 30 * 24 * 60 * 60  # 30 days, in seconds


settings = Settings()