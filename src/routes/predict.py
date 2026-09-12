from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel

from auth.routes import get_current_user
from core.ml_engine import predict_ml
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
    txn_type: str          # ATM_WITHDRAWAL / CASH_DEPOSIT / NACH / NEFT / UPI

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


def _combine(ml_score: float, graph_score: float, has_history: bool) -> tuple[float, str]:
    if has_history:
        blend = FINAL_ML_WEIGHT * ml_score + FINAL_GRAPH_WEIGHT * graph_score
        # Fix 2: agar graph akela hi bahut confident hai (severe pattern jaisa
        # structuring/round_trip), to weighted blend use neeche mat khींchne do.
        # "Agar koi ek detector strongly bolta hai fraud hai, to flag karo" —
        # average karke dabana nahi.
        final_score = round(max(blend, graph_score), 4)
    else:
        # Cold-start: graph ke paas koi opinion nahi, sirf ML pe decide karo
        final_score = round(ml_score, 4)

    if final_score >= 0.7262:
        risk_level = "FRAUD"
    elif final_score >= 0.4357:
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

    ml_result = predict_ml(txn_data)

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
        "saved": not is_guest,
        # Transparency: tells the frontend exactly which soft fields were
        # left blank and got a predefined default instead of user data.
        "defaults_used": txn.defaulted_fields(),
    }