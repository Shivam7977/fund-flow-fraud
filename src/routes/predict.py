from datetime import datetime, timezone
from typing import Literal, Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel

# ml_engine.py ke TYPE_COLUMNS se match — model ne sirf inhi 5 categories pe
# train kiya hai. "RTGS" jaanboojh kar isme nahi hai (training data mein kabhi
# ek alag category nahi thi) — koi aur value aane par one-hot encoding
# silently sab-zero ho jaata, jo model ne kabhi training mein nahi dekha tha.
TxnType = Literal["ATM_WITHDRAWAL", "CASH_DEPOSIT", "NACH", "NEFT", "UPI"]

from auth.routes import get_current_user
from core.ml_engine import predict_ml, THRESHOLDS
from core.graph_engine import score_graph
from core.db import save_prediction
from core.csv_mapper import SOFT_DEFAULTS  # single source of truth for "we don't have this" fallbacks

router = APIRouter(prefix="/predict", tags=["predict"])


class TransactionIn(BaseModel):
    # ---- Mandatory — matches HARD_REQUIRED in core/csv_mapper.py ----
    # The user must always provide these; there is no sensible default
    # for who sent money, who received it, how much, or by what rail.
    nameOrig: str
    nameDest: str
    amount_inr: float
    txn_type: TxnType

    # ---- Optional — matches SOFT_REQUIRED in core/csv_mapper.py ----
    # If the caller doesn't have this data, it's None here and
    # with_defaults() below fills it from SOFT_DEFAULTS — the exact
    # same fallback values the CSV batch pipeline uses, so a single
    # transaction and a CSV row behave identically when data is missing.
    oldbalance_inr: Optional[float] = None
    newbalance_inr: Optional[float] = None
    hour: Optional[int] = None
    day_of_week: Optional[int] = None
    day: Optional[int] = None

    # ---- Optional — NOT part of csv_mapper's soft/hard split, this is
    # new: lets a manual /predict check override the sender's account
    # type (business/current/jan_dhan/savings/student). If left unset,
    # core/ml_engine.py falls back to its static ACCOUNT_TYPE_MAP lookup
    # exactly as before — this never changes CSV/batch behavior.
    account_type: Optional[str] = None

    def with_defaults(self) -> dict:
        data = self.model_dump()
        for field, default in SOFT_DEFAULTS.items():
            if data.get(field) is None:
                data[field] = default
        return data

    def defaulted_fields(self) -> list[str]:
        """Which soft fields the caller left blank (for transparency in the response)."""
        data = self.model_dump()
        return [f for f in SOFT_DEFAULTS if data.get(f) is None]


FINAL_ML_WEIGHT = 0.6
FINAL_GRAPH_WEIGHT = 0.4
# NOTE: yeh ML-vs-graph blend weight hai, ensemble_config.json ke
# andar ke ML-internal weights (isolation_forest/xgboost_v2/exp3) se alag —
# retrain se yeh automatically update nahi hota, jaanboojh kar manual hai.


def _combine(ml_score: float, graph_score: float, has_history: bool) -> tuple[float, str]:
    # Fix 3: has_history=True ka matlab sirf itna hai ki account ki purani
    # transactions maujood hain — iska matlab NAHI ki graph ko unmein kuch
    # suspicious mila. Agar graph_score == 0 (history hai par saaf hai), to
    # blend mein daalne se ML ke apne score ko bewajah 40% tak dilute kar deta
    # tha (0.6*ml + 0.4*0 = 0.6*ml). Ab dono cases — cold-start (history hi
    # nahi) aur "history but clean" (history hai, graph ko kuch nahi mila) —
    # ko ek jaisa treat karte hain: sirf ML pe bharosa karo.
    if has_history and graph_score > 0:
        blend = FINAL_ML_WEIGHT * ml_score + FINAL_GRAPH_WEIGHT * graph_score
        # Fix 2: agar graph akela hi bahut confident hai (severe pattern jaisa
        # structuring/round_trip), to weighted blend use neeche mat khींchne do.
        # "Agar koi ek detector strongly bolta hai fraud hai, to flag karo" —
        # average karke dabana nahi.
        # ml_score bhi isi max() mein rakha hai, taaki ek confident ML score
        # bhi kabhi galti se neeche na khinche — graph sirf score ko UPAR
        # dhakel sakta hai, kabhi neeche nahi.
        final_score = round(max(blend, graph_score, ml_score), 4)
    else:
        final_score = round(ml_score, 4)

    if final_score >= THRESHOLDS["fraud"]:
        risk_level = "FRAUD"
    elif final_score >= THRESHOLDS["review"]:
        risk_level = "REVIEW"
    else:
        risk_level = "NORMAL"
    return final_score, risk_level


@router.post("")
def predict(txn: TransactionIn, request: Request):
    user = get_current_user(request)
    is_guest = user is None or user.get("guest") is True
    user_id = None if is_guest else user["id"]

    txn_data = txn.with_defaults()

    # explain=True: single check mein hamesha SHAP explanation chahiye (ek row, sasta hai)
    ml_result = predict_ml(txn_data, explain=True)

    graph_result = score_graph(
        txn_data["nameOrig"],
        new_txn={
            "nameOrig": txn_data["nameOrig"], "nameDest": txn_data["nameDest"],
            "amount_inr": txn_data["amount_inr"], "day": txn_data["day"], "hour": txn_data["hour"],
        },
    )

    final_score, risk_level = _combine(ml_result["ml_score"], graph_result["graph_score"], graph_result["has_history"])

    if not is_guest:
        save_prediction(
            user_id=user_id, nameOrig=txn_data["nameOrig"], nameDest=txn_data["nameDest"],
            amount_inr=txn_data["amount_inr"], hour=txn_data["hour"], day=txn_data["day"],
            txn_type=txn_data["txn_type"], ml_score=ml_result["ml_score"],
            graph_score=graph_result["graph_score"], final_score=final_score,
            risk_level=risk_level,
            explanation=ml_result.get("explanation"),
            has_history=graph_result["has_history"],
            graph_flags=graph_result["flags"],
        )

    return {
        "final_score": final_score,
        "risk_level": risk_level,
        "ml_score": ml_result["ml_score"],
        "iso_score": ml_result["iso_score"],
        "xgb2_score": ml_result["xgb2_score"],
        "exp3_score": ml_result["exp3_score"],
        "graph_score": graph_result["graph_score"],
        "graph_flags": graph_result["flags"],
        "explanation": ml_result.get("explanation"),
        "has_history": graph_result["has_history"],
        "saved": not is_guest,
        # Transparency: tells the frontend exactly which soft fields were
        # left blank and got a predefined default instead of user data.
        "defaults_used": txn.defaulted_fields(),
    }