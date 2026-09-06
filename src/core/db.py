import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from config import settings


def get_connection():
    """
    Naya connection deta hai. Postgres mein bhi har request ke liye
    naya connection lena simple aur safe hai (abhi ke liye — baad mein
    pooling chahiye to psycopg2.pool ya SQLAlchemy add kar sakte hain).
    """
    conn = psycopg2.connect(settings.DATABASE_URL)
    return conn


def _dict_cursor(conn):
    """RealDictCursor se rows dict jaisa milte hain, sqlite3.Row jaisa hi behavior."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    """
    App start hote waqt ek baar call hoga (app.py se).
    Agar tables already hain toh kuch nahi hoga (IF NOT EXISTS).

    NOTE: nameOrig, nameDest, isFraud_label — ye camelCase/mixed-case
    columns hain. Postgres unquoted identifiers ko automatically
    lowercase kar deta hai, isliye inhe hamesha double-quotes mein
    likhna zaroori hai (CREATE TABLE mein bhi, aur har query mein bhi)
    taaki exact case preserve rahe aur Python code (jo "nameOrig" naam
    se access karta hai) match kare.
    """
    conn = get_connection()
    cur = conn.cursor()

    # ---- users table ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            username TEXT UNIQUE,
            password_hash TEXT,
            auth_provider TEXT NOT NULL DEFAULT 'password',
            is_verified INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
    """)

    # ---- predictions table ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            "nameOrig" TEXT NOT NULL,
            "nameDest" TEXT NOT NULL,
            amount_inr REAL NOT NULL,
            hour INTEGER,
            day INTEGER,
            type TEXT,
            ml_score REAL,
            graph_score REAL,
            final_score REAL,
            risk_level TEXT,
            "isFraud_label" INTEGER,
            predicted_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)

    cur.execute('CREATE INDEX IF NOT EXISTS idx_pred_nameOrig ON predictions("nameOrig");')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_pred_nameDest ON predictions("nameDest");')

    # ---- jobs table ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            user_id INTEGER,
            status TEXT NOT NULL DEFAULT 'processing',
            result_summary TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
    """)

    # ---- pending_signups table ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_signups (
            email TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            otp TEXT NOT NULL,
            otp_created_at TEXT NOT NULL
        );
    """)

    # ---- sessions table ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)

    conn.commit()
    cur.close()
    conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------- Prediction helpers ----------

def save_prediction(user_id, nameOrig, nameDest, amount_inr, hour, day,
                     txn_type, ml_score, graph_score, final_score, risk_level):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO predictions
        (user_id, "nameOrig", "nameDest", amount_inr, hour, day, type,
         ml_score, graph_score, final_score, risk_level, "isFraud_label", predicted_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s)
    """, (user_id, nameOrig, nameDest, amount_inr, hour, day, txn_type,
          ml_score, graph_score, final_score, risk_level, now_iso()))
    conn.commit()
    cur.close()
    conn.close()


def get_account_history(account_id):
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute("""
        SELECT * FROM predictions
        WHERE "nameOrig" = %s OR "nameDest" = %s
        ORDER BY predicted_at DESC
    """, (account_id, account_id))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(row) for row in rows]


# ---------- Job helpers ----------

def create_job(job_id, user_id=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO jobs (job_id, user_id, status, created_at)
        VALUES (%s, %s, 'processing', %s)
    """, (job_id, user_id, now_iso()))
    conn.commit()
    cur.close()
    conn.close()


def update_job(job_id, status, result_summary=None, error_message=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE jobs
        SET status = %s, result_summary = %s, error_message = %s, completed_at = %s
        WHERE job_id = %s
    """, (status, result_summary, error_message, now_iso(), job_id))
    conn.commit()
    cur.close()
    conn.close()


def get_job(job_id):
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def create_pending_signup(email, name, username, password_hash, otp):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO pending_signups (email, name, username, password_hash, otp, otp_created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (email) DO UPDATE SET
            name = EXCLUDED.name,
            username = EXCLUDED.username,
            password_hash = EXCLUDED.password_hash,
            otp = EXCLUDED.otp,
            otp_created_at = EXCLUDED.otp_created_at
    """, (email, name, username, password_hash, otp, now_iso()))
    conn.commit()
    cur.close()
    conn.close()


def get_pending_signup(email):
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute("SELECT * FROM pending_signups WHERE email = %s", (email,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def delete_pending_signup(email):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM pending_signups WHERE email = %s", (email,))
    conn.commit()
    cur.close()
    conn.close()


def create_user(name, email, username, password_hash, auth_provider="password", is_verified=1):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (name, email, username, password_hash, auth_provider, is_verified, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (name, email, username, password_hash, auth_provider, is_verified, now_iso()))
    conn.commit()
    cur.close()
    conn.close()


def get_user_by_email(email):
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute("SELECT * FROM users WHERE email = %s", (email,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def update_user_auth_provider(email, new_provider):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET auth_provider = %s WHERE email = %s", (new_provider, email))
    conn.commit()
    cur.close()
    conn.close()


def init_sessions_table():
    """Ab table init_db() ke andar bhi ban jaati hai, ye function sirf backward-compatibility ke liye rakha hai."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    conn.commit()
    cur.close()
    conn.close()


def create_session(session_id, user_id, expires_at):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sessions (session_id, user_id, created_at, expires_at)
        VALUES (%s, %s, %s, %s)
    """, (session_id, user_id, now_iso(), expires_at))
    conn.commit()
    cur.close()
    conn.close()


def get_session(session_id):
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute("SELECT * FROM sessions WHERE session_id = %s", (session_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def delete_session(session_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))
    conn.commit()
    cur.close()
    conn.close()


def get_user_by_id(user_id):
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None