import time
import hashlib
import psycopg2
import psycopg2.extras
import psycopg2.pool
from datetime import datetime, timezone
from config import settings

# ============================================================
# Connection pooling
# ------------------------------------------------------------
# Ek ThreadedConnectionPool (FastAPI BackgroundTasks thread-pool mein
# chalte hain, isliye Threaded-safe pool chahiye) jo connections
# reuse karta hai. get_connection() pool se leta hai,
# release_connection() wapas pool mein daalta hai (band nahi karta).
# ============================================================

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=settings.DATABASE_URL,
        )
    return _pool


def get_connection(retries: int = 3, delay: float = 0.5):
    """Pool se connection leta hai, transient network/SSL blips ke liye retry-with-backoff ke saath."""
    last_err = None
    for attempt in range(retries):
        try:
            return _get_pool().getconn()
        except psycopg2.OperationalError as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    raise last_err


def release_connection(conn):
    """conn.close() ki jagah ye use karo — connection pool mein wapas chala jaata hai, band nahi hota."""
    if conn is None:
        return
    try:
        _get_pool().putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


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
    likhna zaroori hai taaki exact case preserve rahe.
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

    # ---- password_resets table (Forgot Password — link-based, 1hr expiry) ----
    # NOTE: 'token' column ab hashed value store karta hai, plaintext nahi
    # (dekho create_password_reset/get_password_reset/delete_password_reset).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            token TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
    """)
    cur.execute('CREATE INDEX IF NOT EXISTS idx_password_resets_email ON password_resets(email);')

    # ---- /predict-file feature: naye columns (purane data ko touch nahi karta) ----
    cur.execute('ALTER TABLE jobs ADD COLUMN IF NOT EXISTS filename TEXT;')
    cur.execute('ALTER TABLE jobs ADD COLUMN IF NOT EXISTS total_rows INTEGER;')
    cur.execute('ALTER TABLE jobs ADD COLUMN IF NOT EXISTS file_path TEXT;')
    cur.execute('ALTER TABLE jobs ADD COLUMN IF NOT EXISTS column_mapping TEXT;')
    cur.execute('ALTER TABLE predictions ADD COLUMN IF NOT EXISTS job_id TEXT;')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_pred_job_id ON predictions(job_id);')

    # ---- History search (fuzzy, typo-tolerant) + fast per-user listing ----
    cur.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm;')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_jobs_filename_trgm ON jobs USING GIN (filename gin_trgm_ops);')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON jobs (user_id, created_at DESC);')

    conn.commit()
    cur.close()
    release_connection(conn)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------- Prediction helpers ----------

def save_prediction(user_id, nameOrig, nameDest, amount_inr, hour, day,
                     txn_type, ml_score, graph_score, final_score, risk_level, job_id=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO predictions
        (user_id, "nameOrig", "nameDest", amount_inr, hour, day, type,
         ml_score, graph_score, final_score, risk_level, "isFraud_label", predicted_at, job_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s)
    """, (user_id, nameOrig, nameDest, amount_inr, hour, day, txn_type,
          ml_score, graph_score, final_score, risk_level, now_iso(), job_id))
    conn.commit()
    cur.close()
    release_connection(conn)


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
    release_connection(conn)
    return [dict(row) for row in rows]


def get_predictions_by_job(job_id):
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute('SELECT * FROM predictions WHERE job_id = %s ORDER BY predicted_at', (job_id,))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return [dict(row) for row in rows]


def get_activity_summary(user_id):
    """GET /activity ke liye — existing predictions table se derive, koi naya log table nahi chahiye."""
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute("""
        SELECT risk_level, COUNT(*) as count FROM predictions WHERE user_id = %s GROUP BY risk_level
    """, (user_id,))
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    counts = {row["risk_level"]: row["count"] for row in rows}
    return {"total_predictions": sum(counts.values()), "by_risk_level": counts}


# ---------- Job helpers ----------

def create_job(job_id, user_id=None, filename=None, file_path=None,
               column_mapping=None, total_rows=None, status="processing"):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO jobs (job_id, user_id, status, filename, file_path, column_mapping, total_rows, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (job_id, user_id, status, filename, file_path, column_mapping, total_rows, now_iso()))
    conn.commit()
    cur.close()
    release_connection(conn)


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
    release_connection(conn)


def update_job_mapping(job_id, column_mapping_json):
    """Confirm step ke baad updated mapping save karne ke liye."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE jobs SET column_mapping = %s WHERE job_id = %s", (column_mapping_json, job_id))
    conn.commit()
    cur.close()
    release_connection(conn)


def delete_job_and_predictions(job_id):
    """HARD DELETE — job row aur uske saare associated predictions permanently DB se gayab."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM predictions WHERE job_id = %s", (job_id,))
    cur.execute("DELETE FROM jobs WHERE job_id = %s", (job_id,))
    conn.commit()
    cur.close()
    release_connection(conn)


def get_job(job_id):
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,))
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return dict(row) if row else None


def get_stale_pending_jobs(older_than_iso: str):
    """
    pending_confirmation status mein atke hue purane jobs dhundta hai —
    inka temp CSV file kabhi delete nahi hota kyunki process_file_job()
    sirf success/fail path pe cleanup karta hai. cleanup_stale_pending_jobs()
    isko use karta hai.
    """
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute(
        "SELECT job_id, file_path FROM jobs WHERE status = 'pending_confirmation' AND created_at < %s",
        (older_than_iso,),
    )
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)
    return [dict(row) for row in rows]


def search_jobs(user_id, q=None, status=None, date_from=None, date_to=None, page=1, limit=20):
    """
    Unified history listing — GET /uploads ka single data source:
    search (typo-tolerant via pg_trgm) + status/date filters + pagination,
    sab ek hi query mein. HAMESHA user_id se scoped — caller ye id session
    se nikaale, kabhi client-supplied param se nahi (multi-tenant isolation).

    Response rows mein sirf listing ke liye zaroori columns hain
    (job_id, filename, status, total_rows, created_at) — file_path jaisa
    internal server path kabhi return nahi hota.
    """
    page = max(1, page)
    limit = max(1, min(limit, 100))
    offset = (page - 1) * limit

    clean_q = (q or "").strip()
    use_search = len(clean_q) >= 2

    where_clauses = ["user_id = %(user_id)s"]
    params = {"user_id": user_id, "limit": limit, "offset": offset}

    if use_search:
        params["q"] = clean_q
        params["exact"] = clean_q
        params["prefix"] = clean_q + "%"
        params["substr"] = f"%{clean_q}%"
        where_clauses.append("(filename ILIKE %(substr)s OR similarity(filename, %(q)s) > 0.2)")

    if status:
        params["status"] = status
        where_clauses.append("status = %(status)s")

    if date_from:
        params["date_from"] = date_from
        where_clauses.append("created_at >= %(date_from)s")

    if date_to:
        params["date_to"] = date_to
        where_clauses.append("created_at <= %(date_to)s")

    where_sql = " AND ".join(where_clauses)

    if use_search:
        rank_select = """
            CASE
                WHEN filename ILIKE %(exact)s THEN 4
                WHEN filename ILIKE %(prefix)s THEN 3
                WHEN filename ILIKE %(substr)s THEN 2
                ELSE 1
            END AS match_rank,
            similarity(filename, %(q)s) AS sim_score,
        """
        order_sql = "ORDER BY match_rank DESC, sim_score DESC, created_at DESC"
    else:
        rank_select = ""
        order_sql = "ORDER BY created_at DESC"

    query = f"""
        SELECT job_id, filename, status, total_rows, created_at,
               {rank_select}
               COUNT(*) OVER() AS total_count
        FROM jobs
        WHERE {where_sql}
        {order_sql}
        LIMIT %(limit)s OFFSET %(offset)s
    """

    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    release_connection(conn)

    items = [dict(r) for r in rows]
    total = items[0]["total_count"] if items else 0
    for it in items:
        it.pop("total_count", None)
        it.pop("match_rank", None)
        it.pop("sim_score", None)

    total_pages = max(1, (total + limit - 1) // limit)
    return {"uploads": items, "page": page, "limit": limit, "total": total, "total_pages": total_pages}


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
    release_connection(conn)


def get_pending_signup(email):
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute("SELECT * FROM pending_signups WHERE email = %s", (email,))
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return dict(row) if row else None


def delete_pending_signup(email):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM pending_signups WHERE email = %s", (email,))
    conn.commit()
    cur.close()
    release_connection(conn)


def create_user(name, email, username, password_hash, auth_provider="password", is_verified=1):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (name, email, username, password_hash, auth_provider, is_verified, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (name, email, username, password_hash, auth_provider, is_verified, now_iso()))
    conn.commit()
    cur.close()
    release_connection(conn)


def get_user_by_email(email):
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute("SELECT * FROM users WHERE email = %s", (email,))
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return dict(row) if row else None


def update_user_auth_provider(email, new_provider):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET auth_provider = %s WHERE email = %s", (new_provider, email))
    conn.commit()
    cur.close()
    release_connection(conn)


def update_user_password(email, password_hash):
    """Add Password aur Reset Password dono isi function se hote hain."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET password_hash = %s WHERE email = %s", (password_hash, email))
    conn.commit()
    cur.close()
    release_connection(conn)


def create_session(session_id, user_id, expires_at):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sessions (session_id, user_id, created_at, expires_at)
        VALUES (%s, %s, %s, %s)
    """, (session_id, user_id, now_iso(), expires_at))
    conn.commit()
    cur.close()
    release_connection(conn)


def get_session(session_id):
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute("SELECT * FROM sessions WHERE session_id = %s", (session_id,))
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return dict(row) if row else None


def delete_session(session_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))
    conn.commit()
    cur.close()
    release_connection(conn)


def get_user_by_id(user_id):
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return dict(row) if row else None


# ---------- Password reset helpers (Forgot Password — link-based) ----------
# SECURITY: token DB mein PLAINTEXT nahi, SHA-256 hash store hota hai.
# Agar kabhi DB leak ho, purane reset links directly usable nahi honge
# (bilkul password_hash jaisa hi principle — bearer secret ko hash karke
# store karo, raw value sirf email mein bheja jaata hai).

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_password_reset(token, email, expires_at):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO password_resets (token, email, created_at, expires_at)
        VALUES (%s, %s, %s, %s)
    """, (_hash_token(token), email, now_iso(), expires_at))
    conn.commit()
    cur.close()
    release_connection(conn)


def get_password_reset(token):
    conn = get_connection()
    cur = _dict_cursor(conn)
    cur.execute("SELECT * FROM password_resets WHERE token = %s", (_hash_token(token),))
    row = cur.fetchone()
    cur.close()
    release_connection(conn)
    return dict(row) if row else None


def delete_password_reset(token):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM password_resets WHERE token = %s", (_hash_token(token),))
    conn.commit()
    cur.close()
    release_connection(conn)


def delete_password_resets_for_email(email):
    """Naya reset request aane par purane pending tokens usi email ke saaf kar deta hai."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM password_resets WHERE email = %s", (email,))
    conn.commit()
    cur.close()
    release_connection(conn)