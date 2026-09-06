from fastapi import APIRouter, Request
from pydantic import BaseModel

from auth.routes import get_current_user
from core.ml_engine import predict_ml
from core.graph_engine import score_graph
from core.db import save_prediction
from routes.predict import TransactionIn, _combine

router = APIRouter(prefix="/predict", tags=["predict"])


class BatchTransactionsIn(BaseModel):
    transactions: list[TransactionIn]


@router.post("/batch")
def predict_batch(batch: BatchTransactionsIn, request: Request):
    user = get_current_user(request)
    is_guest = user is None or user.get("guest") is True
    user_id = None if is_guest else user["id"]

    results = []
    seen_txns = []      # isi batch ke andar ab tak process ho chuke transactions (context ke liye)
    to_save = []        # saving DEFER ki hui hai — batch khatam hone ke baad ek saath save hoga,
                         # warna DB history + batch context dono se same txn double-count ho jaata

    for txn in batch.transactions:
        # NOTE: get_account_history() (DB se) is batch ke pehle-save-hue transactions kabhi
        # nahi dekhega, kyunki hum save END mein kar rahe hain — sirf seen_txns hi context deta hai
        ml_result = predict_ml(txn.model_dump(), batch_history=seen_txns)

        graph_result = score_graph(
            txn.nameOrig,
            new_txn={
                "nameOrig": txn.nameOrig, "nameDest": txn.nameDest,
                "amount_inr": txn.amount_inr, "day": txn.day, "hour": txn.hour,
            },
            batch_txns=seen_txns,
        )

        final_score, risk_level = _combine(
            ml_result["ml_score"], graph_result["graph_score"], graph_result["has_history"]
        )

        results.append({
            "nameOrig": txn.nameOrig,
            "nameDest": txn.nameDest,
            "final_score": final_score,
            "risk_level": risk_level,
            "ml_score": ml_result["ml_score"],
            "graph_score": graph_result["graph_score"],
            "graph_flags": graph_result["flags"],
        })

        if not is_guest:
            to_save.append(dict(
                user_id=user_id, nameOrig=txn.nameOrig, nameDest=txn.nameDest,
                amount_inr=txn.amount_inr, hour=txn.hour, day=txn.day,
                txn_type=txn.txn_type, ml_score=ml_result["ml_score"],
                graph_score=graph_result["graph_score"], final_score=final_score,
                risk_level=risk_level,
            ))

        # Agle transaction ke liye batch context mein daal do
        seen_txns.append({
            "nameOrig": txn.nameOrig, "nameDest": txn.nameDest,
            "amount_inr": txn.amount_inr, "day": txn.day, "hour": txn.hour,
        })

    # Ab poora batch process ho chuka — sabko ek saath DB mein save karo
    for row in to_save:
        save_prediction(**row)

    return {
        "count": len(results),
        "saved": not is_guest,
        "results": results,
    }