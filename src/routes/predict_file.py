import os
import json
import uuid
import pandas as pd
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, UploadFile, File, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from config import settings
from auth.routes import get_current_user
from core.ml_engine import predict_ml
from core.graph_engine import score_graph
from core.db import (
    create_job, update_job, update_job_mapping, get_job, search_jobs, get_stale_pending_jobs,
    save_prediction, get_predictions_by_job, get_activity_summary, delete_job_and_predictions,
)
from core.csv_mapper import analyze_columns, SOFT_REQUIRED, SOFT_DEFAULTS
from routes.predict import _combine

router = APIRouter(prefix="/predict", tags=["predict"])
uploads_router = APIRouter(tags=["uploads"])

# Temp CSV files yahan rakhe jaayenge (confirmation ke wait mein, ya processing ke dauraan)
TMP_UPLOAD_DIR = os.path.join(os.path.dirname(settings.STATIC_CSV_PATH), "tmp_uploads")
os.makedirs(TMP_UPLOAD_DIR, exist_ok=True)

# Ek upload itna bada nahi hona chahiye ki poori file memory mein load
# hote hi (pd.read_csv) server ka RAM khatam kar de. 10MB kaafi generous
# hai chhote-medium CSVs ke liye (demo/college-project scale).
MAX_CSV_SIZE_BYTES = 10 * 1024 * 1024

# Kitni der tak "pending_confirmation" job abandon maana jaaye (user
# confirm karna bhool gaya) — cleanup_stale_pending_jobs() isse compare
# karta hai.
STALE_PENDING_HOURS = 24


class ConfirmIn(BaseModel):
    column_mapping: dict | None = None      # e.g. {"hour": "transaction_hour"}
    proceed_with_defaults: bool = False      # true = missing optional fields ko default value se chalao


# ---------- Background processing ----------

def process_file_job(job_id: str, file_path: str, mapping: dict, user_id, is_guest: bool):
    """
    Poori CSV ko process karta hai — /predict-batch jaisa hi batch-context logic
    (seen_txns), taaki same-file ke andar bhi fan-out/structuring bina DB
    round-trip ke detect ho. Saving loop khatam hone ke BAAD hoti hai
    (double-counting se bachne ke liye, jaisa /predict-batch mein fix kiya tha).
    """
    try:
        df = pd.read_csv(file_path)
        seen_txns = []
        to_save = []
        fraud_count = review_count = normal_count = 0

        for _, r in df.iterrows():
            txn = {
                "nameOrig": str(r[mapping["nameOrig"]]),
                "nameDest": str(r[mapping["nameDest"]]),
                "amount_inr": float(r[mapping["amount_inr"]]),
                "txn_type": str(r[mapping["txn_type"]]),
                "oldbalance_inr": float(r[mapping["oldbalance_inr"]]) if mapping.get("oldbalance_inr") else SOFT_DEFAULTS["oldbalance_inr"],
                "newbalance_inr": float(r[mapping["newbalance_inr"]]) if mapping.get("newbalance_inr") else SOFT_DEFAULTS["newbalance_inr"],
                "hour": int(r[mapping["hour"]]) if mapping.get("hour") else SOFT_DEFAULTS["hour"],
                "day_of_week": int(r[mapping["day_of_week"]]) if mapping.get("day_of_week") else SOFT_DEFAULTS["day_of_week"],
                "day": int(r[mapping["day"]]) if mapping.get("day") else SOFT_DEFAULTS["day"],
            }

            ml_result = predict_ml(txn, batch_history=seen_txns)
            graph_result = score_graph(
                txn["nameOrig"],
                new_txn={
                    "nameOrig": txn["nameOrig"], "nameDest": txn["nameDest"],
                    "amount_inr": txn["amount_inr"], "day": txn["day"], "hour": txn["hour"],
                },
                batch_txns=seen_txns,
            )
            final_score, risk_level = _combine(
                ml_result["ml_score"], graph_result["graph_score"], graph_result["has_history"]
            )

            if risk_level == "FRAUD":
                fraud_count += 1
            elif risk_level == "REVIEW":
                review_count += 1
            else:
                normal_count += 1

            if not is_guest:
                to_save.append(dict(
                    user_id=user_id, nameOrig=txn["nameOrig"], nameDest=txn["nameDest"],
                    amount_inr=txn["amount_inr"], hour=txn["hour"], day=txn["day"],
                    txn_type=txn["txn_type"], ml_score=ml_result["ml_score"],
                    graph_score=graph_result["graph_score"], final_score=final_score,
                    risk_level=risk_level, job_id=job_id,
                ))

            seen_txns.append({
                "nameOrig": txn["nameOrig"], "nameDest": txn["nameDest"],
                "amount_inr": txn["amount_inr"], "day": txn["day"], "hour": txn["hour"],
            })

        for row in to_save:
            save_prediction(**row)

        summary = {"total": len(df), "fraud": fraud_count, "review": review_count, "normal": normal_count}
        update_job(job_id, status="done", result_summary=json.dumps(summary))

    except Exception as e:
        update_job(job_id, status="failed", error_message=str(e))

    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


def cleanup_stale_pending_jobs():
    """
    Jo jobs 'pending_confirmation' mein atke reh gaye (user confirm karna
    bhool gaya, tab browser band kar diya) — unka temp CSV file kabhi
    delete nahi hota, sirf process_file_job() ke success/fail path pe
    cleanup hota hai. Ye function un purane pending jobs ko 'failed' mark
    karke unka temp file disk se hata deta hai.

    App startup pe ek baar call karo (app.py mein, init_db() ke paas):

        from routes.predict_file import cleanup_stale_pending_jobs
        cleanup_stale_pending_jobs()

    Chaho to isse periodic bhi bana sakte ho (APScheduler jaisa kuch),
    abhi ke liye startup-time cleanup kaafi hai.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=STALE_PENDING_HOURS)).isoformat()
    stale_jobs = get_stale_pending_jobs(cutoff)

    for job in stale_jobs:
        file_path = job.get("file_path")
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError as e:
                print(f"[predict_file] Could not remove stale temp file {file_path}: {e}")
        update_job(job["job_id"], status="failed", error_message="Upload was never confirmed — auto-cleaned up.")

    if stale_jobs:
        print(f"[predict_file] Cleaned up {len(stale_jobs)} stale pending_confirmation job(s).")


# ---------- Upload endpoint ----------

@router.post("/file")
async def upload_file(request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")

    # File size cap — poori CSV memory mein load hone se pehle reject
    # kar do agar bahut badi hai. file.file ek SpooledTemporaryFile hai,
    # seek/tell dono version-independent tarike se kaam karte hain.
    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)
    if file_size > MAX_CSV_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max allowed size is {MAX_CSV_SIZE_BYTES // (1024 * 1024)}MB.",
        )

    user = get_current_user(request)
    is_guest = user is None or user.get("guest") is True
    user_id = None if is_guest else user["id"]

    df = pd.read_csv(file.file)
    analysis = analyze_columns(list(df.columns))

    if analysis["missing_hard"]:
        raise HTTPException(status_code=422, detail={
            "error": "Required columns not found in CSV",
            "missing_required": analysis["missing_hard"],
            "detected_columns": list(df.columns),
        })

    job_id = str(uuid.uuid4())
    temp_path = os.path.join(TMP_UPLOAD_DIR, f"{job_id}.csv")
    df.to_csv(temp_path, index=False)

    if analysis["missing_soft"]:
        # Optional fields missing — RUKO, user se confirm karwao, assume mat karo
        create_job(
            job_id=job_id, user_id=user_id, filename=file.filename,
            file_path=temp_path, column_mapping=json.dumps(analysis["mapping"]),
            total_rows=len(df), status="pending_confirmation",
        )
        return {
            "job_id": job_id,
            "status": "pending_confirmation",
            "message": (
                f"Some optional columns were not found in the CSV. Provide their "
                f"data via column_mapping at POST /predict/file/{job_id}/confirm, "
                "or send proceed_with_defaults: true to continue with safe defaults."
            ),
            "missing_optional_fields": analysis["missing_soft"],
            "detected_mapping": analysis["mapping"],
        }

    # Sab kuch mapped mil gaya — seedha processing shuru
    create_job(
        job_id=job_id, user_id=user_id, filename=file.filename,
        file_path=temp_path, column_mapping=json.dumps(analysis["mapping"]),
        total_rows=len(df), status="processing",
    )
    background_tasks.add_task(process_file_job, job_id, temp_path, analysis["mapping"], user_id, is_guest)
    return {"job_id": job_id, "status": "processing", "total_rows": len(df)}


@router.post("/file/{job_id}/confirm")
def confirm_file(job_id: str, body: ConfirmIn, request: Request, background_tasks: BackgroundTasks):
    job = get_job(job_id)
    if not job or job["status"] != "pending_confirmation":
        raise HTTPException(status_code=404, detail="Job not found or already processed")

    user = get_current_user(request)
    is_guest = user is None or user.get("guest") is True

    stored_mapping = json.loads(job["column_mapping"]) if job.get("column_mapping") else {}
    if body.column_mapping:
        stored_mapping.update(body.column_mapping)

    still_missing = [f for f in SOFT_REQUIRED if not stored_mapping.get(f)]

    if still_missing and not body.proceed_with_defaults:
        return {
            "job_id": job_id,
            "status": "pending_confirmation",
            "message": "These fields are still missing. Provide column_mapping or send proceed_with_defaults: true.",
            "missing_optional_fields": still_missing,
        }

    update_job_mapping(job_id, json.dumps(stored_mapping))
    update_job(job_id, status="processing")
    background_tasks.add_task(process_file_job, job_id, job["file_path"], stored_mapping, job["user_id"], is_guest)
    return {"job_id": job_id, "status": "processing"}


@router.get("/file/{job_id}")
def get_file_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ---------- Upload history (search + filter + pagination, unified) ----------

@uploads_router.get("/uploads")
def list_uploads(
    request: Request,
    q: str | None = Query(None, description="Filename search — fuzzy/typo-tolerant, needs 2+ chars to activate"),
    status: str | None = Query(None, description="Filter by job status, e.g. done/processing/failed"),
    date_from: str | None = Query(None, description="ISO date — jobs created on/after this"),
    date_to: str | None = Query(None, description="ISO date — jobs created on/before this"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Single history endpoint — search, status/date filters aur pagination
    sab isi mein. user_id HAMESHA session se aata hai (get_current_user),
    kabhi query param se nahi — isliye ek user kabhi doosre ki files nahi
    dekh sakta, chahe wo kuch bhi bheje.
    """
    user = get_current_user(request)
    if user is None or user.get("guest") is True:
        raise HTTPException(status_code=401, detail="Login required to view upload history")

    return search_jobs(
        user["id"], q=q, status=status, date_from=date_from, date_to=date_to,
        page=page, limit=limit,
    )


@uploads_router.get("/uploads/{job_id}")
def get_upload_detail(job_id: str, request: Request):
    user = get_current_user(request)
    if user is None or user.get("guest") is True:
        raise HTTPException(status_code=401, detail="Login required")

    job = get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Upload not found")

    return {"job": job, "predictions": get_predictions_by_job(job_id)}


@uploads_router.delete("/uploads/{job_id}")
def delete_upload(job_id: str, request: Request):
    """
    HARD DELETE — job aur uske saare predictions permanently gayab.
    Undo nahi ho sakta, isliye frontend confirm dialog dikhata hai
    delete call se pehle.
    """
    user = get_current_user(request)
    if user is None or user.get("guest") is True:
        raise HTTPException(status_code=401, detail="Login required")

    job = get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Upload not found")

    delete_job_and_predictions(job_id)
    return {"status": "ok", "message": "Job permanently deleted"}


@uploads_router.get("/activity")
def get_activity(request: Request):
    user = get_current_user(request)
    if user is None or user.get("guest") is True:
        raise HTTPException(status_code=401, detail="Login required")
    return get_activity_summary(user["id"])