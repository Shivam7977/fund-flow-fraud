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
        # BUG FIX: txn.model_dump() raw Pydantic fields deta hai — agar koi
        # optional field (oldbalance_inr/hour/day_of_week/day) caller ne nahi
        # di, woh None rehta tha, aur build_features() mein float(None) se
        # crash ho jaata tha. with_defaults() single-/predict aur /predict-file
        # dono jaisa hi SOFT_DEFAULTS laga deta hai — ab teeno entry-points
        # consistent hain.
        txn_data = txn.with_defaults()

        # NOTE: get_account_history() (DB se) is batch ke pehle-save-hue transactions kabhi
        # nahi dekhega, kyunki hum save END mein kar rahe hain — sirf seen_txns hi context deta hai
        # explain=True: SHAP hamesha compute hota hai (ek extra XGBoost predict call, sasta hai),
        # taaki har row ke liye get_account_history() ko dobara call na karna pade agar hum
        # sirf flagged rows ke liye alag se explanation banate. Neeche sirf FRAUD/REVIEW
        # rows ke explanation ko save/response mein rakha jaata hai (DB size control ke liye).
        ml_result = predict_ml(txn_data, batch_history=seen_txns, explain=True)

        graph_result = score_graph(
            txn_data["nameOrig"],
            new_txn={
                "nameOrig": txn_data["nameOrig"], "nameDest": txn_data["nameDest"],
                "amount_inr": txn_data["amount_inr"], "day": txn_data["day"], "hour": txn_data["hour"],
            },
            batch_txns=seen_txns,
        )

        final_score, risk_level = _combine(
            ml_result["ml_score"], graph_result["graph_score"], graph_result["has_history"]
        )

        # Sirf flagged rows ka explanation rakhte hain — NORMAL rows ke liye discard,
        # taaki DB mein bewajah bada JSONB na bhare.
        explanation = ml_result.get("explanation") if risk_level in ("FRAUD", "REVIEW") else None

        results.append({
            "nameOrig": txn.nameOrig,
            "nameDest": txn.nameDest,
            "final_score": final_score,
            "risk_level": risk_level,
            "ml_score": ml_result["ml_score"],
            "graph_score": graph_result["graph_score"],
            "graph_flags": graph_result["flags"],
            "explanation": explanation,
            "has_history": graph_result["has_history"],
        })

        if not is_guest:
            to_save.append(dict(
                user_id=user_id, nameOrig=txn_data["nameOrig"], nameDest=txn_data["nameDest"],
                amount_inr=txn_data["amount_inr"], hour=txn_data["hour"], day=txn_data["day"],
                txn_type=txn_data["txn_type"], ml_score=ml_result["ml_score"],
                graph_score=graph_result["graph_score"], final_score=final_score,
                risk_level=risk_level, explanation=explanation, has_history=graph_result["has_history"],
                graph_flags=graph_result["flags"],
            ))

        # Agle transaction ke liye batch context mein daal do — txn_data (defaults-applied)
        # use karna zaroori hai, warna ek None hour/day seen_txns mein chala jaata aur
        # graph_engine.py ke .get("hour", 0) ko bhi dhokha deta (key maujood hai, value None).
        seen_txns.append({
            "nameOrig": txn_data["nameOrig"], "nameDest": txn_data["nameDest"],
            "amount_inr": txn_data["amount_inr"], "day": txn_data["day"], "hour": txn_data["hour"],
        })

    # Ab poora batch process ho chuka — sabko ek saath DB mein save karo
    for row in to_save:
        save_prediction(**row)

    return {
        "count": len(results),
        "saved": not is_guest,
        "results": results,
    }