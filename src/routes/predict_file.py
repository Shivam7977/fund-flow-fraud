import os
import json
import uuid
import pandas as pd
from fastapi import APIRouter, Request, UploadFile, File, BackgroundTasks, HTTPException
from pydantic import BaseModel

from config import settings
from auth.routes import get_current_user
from core.ml_engine import predict_ml
from core.graph_engine import score_graph
from core.db import (
    create_job, update_job, update_job_mapping, get_job, get_jobs_by_user,
    save_prediction, get_predictions_by_job, get_activity_summary,
)
from core.csv_mapper import analyze_columns, SOFT_REQUIRED, SOFT_DEFAULTS
from routes.predict import _combine

router = APIRouter(prefix="/predict", tags=["predict"])
uploads_router = APIRouter(tags=["uploads"])

# Temp CSV files yahan rakhe jaayenge (confirmation ke wait mein, ya processing ke dauraan)
TMP_UPLOAD_DIR = os.path.join(os.path.dirname(settings.STATIC_CSV_PATH), "tmp_uploads")
os.makedirs(TMP_UPLOAD_DIR, exist_ok=True)


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


# ---------- Upload endpoint ----------

@router.post("/file")
async def upload_file(request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Sirf CSV files supported hain")

    user = get_current_user(request)
    is_guest = user is None or user.get("guest") is True
    user_id = None if is_guest else user["id"]

    df = pd.read_csv(file.file)
    analysis = analyze_columns(list(df.columns))

    if analysis["missing_hard"]:
        raise HTTPException(status_code=422, detail={
            "error": "Zaroori columns CSV mein nahi mile",
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
                "Kuch optional columns CSV mein nahi mile. Inka data POST "
                f"/predict/file/{job_id}/confirm mein column_mapping se bhejo, "
                "ya proceed_with_defaults: true bhejo taaki safe default values se aage badhein."
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
        raise HTTPException(status_code=404, detail="Job nahi mila ya already process ho chuka hai")

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
            "message": "Abhi bhi ye fields missing hain. column_mapping do ya proceed_with_defaults: true bhejo.",
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
        raise HTTPException(status_code=404, detail="Job nahi mila")
    return job


# ---------- Upload history ----------

@uploads_router.get("/uploads")
def list_uploads(request: Request):
    user = get_current_user(request)
    if user is None or user.get("guest") is True:
        raise HTTPException(status_code=401, detail="Upload history dekhne ke liye login zaroori hai")
    return {"uploads": get_jobs_by_user(user["id"])}


@uploads_router.get("/uploads/{job_id}")
def get_upload_detail(job_id: str, request: Request):
    user = get_current_user(request)
    if user is None or user.get("guest") is True:
        raise HTTPException(status_code=401, detail="Login zaroori hai")

    job = get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Upload nahi mila")

    return {"job": job, "predictions": get_predictions_by_job(job_id)}


@uploads_router.get("/activity")
def get_activity(request: Request):
    user = get_current_user(request)
    if user is None or user.get("guest") is True:
        raise HTTPException(status_code=401, detail="Login zaroori hai")
    return get_activity_summary(user["id"])