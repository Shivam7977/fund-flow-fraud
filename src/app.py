from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from config import settings
from core.db import init_db
from auth.routes import router as auth_router
from routes.predict import router as predict_router
from routes.predict_batch import router as predict_batch_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — app chalu hote hi ek baar
    init_db()
    print("✅ Database ready")
    yield
    # Shutdown (abhi kuch cleanup nahi chahiye)


app = FastAPI(
    title="Fund Flow Fraud Detection",
    description="Real-time ML + Graph based transaction fraud detection",
    version="1.0.0",
    lifespan=lifespan,
)

# Session cookies (login/guest state) ke liye zaroori — Google OAuth aur session_id cookie dono isi pe depend karte hain
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

# CORS — abhi development mein sab allow, baad mein frontend domain specific kar denge
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(predict_router)
app.include_router(predict_batch_router)


@app.get("/")
def root():
    return {"status": "ok", "message": "Fund Flow Fraud Detection API running"}


@app.get("/health")
def health():
    return {"status": "healthy"}