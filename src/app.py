from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from config import settings
from core.db import init_db, get_connection, release_connection
from auth.routes import router as auth_router
from routes.predict import router as predict_router
from routes.predict_batch import router as predict_batch_router
from routes.predict_file import router as predict_file_router, uploads_router
from routes.graph import router as graph_router
from routes.pages import router as pages_router
from routes.dashboard_pages import router as dashboard_pages_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — app chalu hote hi ek baar
    init_db()
    from routes.predict_file import cleanup_stale_pending_jobs
    cleanup_stale_pending_jobs()
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

# Frontend static files (CSS/JS) — src/static/ se serve honge
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth_router)
app.include_router(predict_router)
app.include_router(predict_batch_router)
app.include_router(predict_file_router)
app.include_router(uploads_router)
app.include_router(graph_router)
app.include_router(pages_router)
app.include_router(dashboard_pages_router)


# Uptime-monitor endpoints. HEAD is allowed too, because some free monitors
# (e.g. UptimeRobot) probe with HEAD and would otherwise get "405 Method Not Allowed".
@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    """Lightweight liveness check (no database). Safe to use as Render's Health Check Path."""
    return {"status": "healthy"}


@app.api_route("/health/db", methods=["GET", "HEAD"])
def health_db():
    """
    Also touches PostgreSQL. get_connection() pre-pings the database, so a monitor
    hitting this endpoint keeps the free Aiven instance awake as well, and reports
    DOWN (503) if the database cannot be reached.
    """
    conn = None
    try:
        conn = get_connection()
        return {"status": "healthy", "database": "up"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unhealthy", "database": "down"})
    finally:
        release_connection(conn)