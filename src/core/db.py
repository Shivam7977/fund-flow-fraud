import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from config import settings


def get_connection():
    """
    Naya connection deta hai. SQLite mein har request/thread ke liye
    naya connection lena safe practice hai (FastAPI multiple threads
    use karta hai sync routes ke liye).
    """
    Path(settings.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.DATABASE_PATH)
    conn.row_factory = sqlite3.Row   # rows ko dict jaisa access karne ke liye
    conn.execute("PRAGMA journal_mode=WAL;")  # concurrent read/write safer
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db():
    """
    App start hote waqt ek baar call hoga (app.py se).
    Agar tables already hain toh kuch nahi hoga (IF NOT EXISTS).
    """
    conn = get_connection()
    cur = conn.cursor()

    # ---- users table ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            username TEXT UNIQUE,
            password_hash TEXT,               -- NULL agar sirf Google se signup hua
            auth_provider TEXT NOT NULL DEFAULT 'password',  -- 'password' / 'google' / 'both'
            is_verified INTEGER NOT NULL DEFAULT 0,          -- OTP verify hua ya nahi
            created_at TEXT NOT NULL
        );
    """)

    # ---- predictions table ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,                  -- NULL agar guest ne kiya (guest ka save hi nahi hoga, but column rakha)
            nameOrig TEXT NOT NULL,
            nameDest TEXT NOT NULL,
            amount_inr REAL NOT NULL,
            hour INTEGER,
            day INTEGER,
            type TEXT,
            ml_score REAL,
            graph_score REAL,
            final_score REAL,
            risk_level TEXT,
            isFraud_label INTEGER,            -- NULL = unknown, 0/1 = confirmed later
            predicted_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)

    # Fast lookup jab account history nikaalni ho local subgraph ke liye
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pred_nameOrig ON predictions(nameOrig);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pred_nameDest ON predictions(nameDest);")

    # ---- jobs table (background file processing) ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            user_id INTEGER,
            status TEXT NOT NULL DEFAULT 'processing',  -- processing / done / failed
            result_summary TEXT,               -- JSON string, chhota summary (counts, graph_html_url)
            error_message TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
    """)

    conn.commit()
    conn.close()


def now_iso():
    """Consistent timestamp format sab jagah use karne ke liye."""
    return datetime.now(timezone.utc).isoformat()


# ---------- Prediction helpers ----------

def save_prediction(user_id, nameOrig, nameDest, amount_inr, hour, day,
                     txn_type, ml_score, graph_score, final_score, risk_level):
    conn = get_connection()
    conn.execute("""
        INSERT INTO predictions
        (user_id, nameOrig, nameDest, amount_inr, hour, day, type,
         ml_score, graph_score, final_score, risk_level, isFraud_label, predicted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
    """, (user_id, nameOrig, nameDest, amount_inr, hour, day, txn_type,
          ml_score, graph_score, final_score, risk_level, now_iso()))
    conn.commit()
    conn.close()


def get_account_history(account_id):
    """
    Ek account (sender ya receiver) ki saari persisted transactions
    nikaalta hai — local subgraph banane ke liye use hoga.
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM predictions
        WHERE nameOrig = ? OR nameDest = ?
        ORDER BY predicted_at DESC
    """, (account_id, account_id)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------- Job helpers ----------

def create_job(job_id, user_id=None):
    conn = get_connection()
    conn.execute("""
        INSERT INTO jobs (job_id, user_id, status, created_at)
        VALUES (?, ?, 'processing', ?)
    """, (job_id, user_id, now_iso()))
    conn.commit()
    conn.close()


def update_job(job_id, status, result_summary=None, error_message=None):
    conn = get_connection()
    conn.execute("""
        UPDATE jobs
        SET status = ?, result_summary = ?, error_message = ?, completed_at = ?
        WHERE job_id = ?
    """, (status, result_summary, error_message, now_iso(), job_id))
    conn.commit()
    conn.close()


def get_job(job_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def create_pending_signup(email, name, username, password_hash, otp):
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_signups (
            email TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            otp TEXT NOT NULL,
            otp_created_at TEXT NOT NULL
        );
    """)
    conn.execute("""
        INSERT OR REPLACE INTO pending_signups (email, name, username, password_hash, otp, otp_created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (email, name, username, password_hash, otp, now_iso()))
    conn.commit()
    conn.close()


def get_pending_signup(email):
    conn = get_connection()
    row = conn.execute("SELECT * FROM pending_signups WHERE email = ?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_pending_signup(email):
    conn = get_connection()
    conn.execute("DELETE FROM pending_signups WHERE email = ?", (email,))
    conn.commit()
    conn.close()


def create_user(name, email, username, password_hash, auth_provider="password", is_verified=1):
    conn = get_connection()
    conn.execute("""
        INSERT INTO users (name, email, username, password_hash, auth_provider, is_verified, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (name, email, username, password_hash, auth_provider, is_verified, now_iso()))
    conn.commit()
    conn.close()


def get_user_by_email(email):
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_user_auth_provider(email, new_provider):
    """Jab same email password aur Google dono se login kare — 'both' set karo."""
    conn = get_connection()
    conn.execute("UPDATE users SET auth_provider = ? WHERE email = ?", (new_provider, email))
    conn.commit()
    conn.close()
    
def init_sessions_table():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    conn.commit()
    conn.close()


def create_session(session_id, user_id, expires_at):
    conn = get_connection()
    conn.execute("""
        INSERT INTO sessions (session_id, user_id, created_at, expires_at)
        VALUES (?, ?, ?, ?)
    """, (session_id, user_id, now_iso(), expires_at))
    conn.commit()
    conn.close()


def get_session(session_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_session(session_id):
    conn = get_connection()
    conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()


def get_user_by_id(user_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None    