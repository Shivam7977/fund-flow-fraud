from datetime import datetime, timezone
from fastapi import APIRouter, Request
from pydantic import BaseModel

from auth.routes import get_current_user
from core.ml_engine import predict_ml
from core.graph_engine import score_graph
from core.db import save_prediction

router = APIRouter(prefix="/predict", tags=["predict"])


class TransactionIn(BaseModel):
    nameOrig: str
    nameDest: str
    amount_inr: float
    oldbalance_inr: float
    newbalance_inr: float
    hour: int
    day_of_week: int
    day: int              # graph engine ke liye (fan-out/in same-day grouping)
    txn_type: str          # ATM_WITHDRAWAL / CASH_DEPOSIT / NACH / NEFT / UPI


FINAL_ML_WEIGHT = 0.6
FINAL_GRAPH_WEIGHT = 0.4



def _combine(ml_score: float, graph_score: float, has_history: bool) -> tuple[float, str]:
    if has_history:
        final_score = round(FINAL_ML_WEIGHT * ml_score + FINAL_GRAPH_WEIGHT * graph_score, 4)
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

    ml_result = predict_ml(txn.model_dump())

    graph_result = score_graph(
        txn.nameOrig,
        new_txn={
            "nameOrig": txn.nameOrig, "nameDest": txn.nameDest,
            "amount_inr": txn.amount_inr, "day": txn.day, "hour": txn.hour,
        },
    )

    final_score, risk_level = _combine(ml_result["ml_score"], graph_result["graph_score"], graph_result["has_history"])

    if not is_guest:
        save_prediction(
            user_id=user_id, nameOrig=txn.nameOrig, nameDest=txn.nameDest,
            amount_inr=txn.amount_inr, hour=txn.hour, day=txn.day,
            txn_type=txn.txn_type, ml_score=ml_result["ml_score"],
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
    }