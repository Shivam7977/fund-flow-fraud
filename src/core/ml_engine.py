import os
import json
import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from config import settings
from core.db import get_account_history

# ---------- Load once, at import time ----------

with open(os.path.join(settings.MODELS_DIR, "ensemble_config.json")) as f:
    CONFIG = json.load(f)

WEIGHTS = CONFIG["weights"]
THRESHOLDS = CONFIG["thresholds"]
FEATURES_XGB2 = CONFIG["features"]["xgboost_v2"]      # 30 features, in training order
FEATURES_EXP3 = CONFIG["features"]["xgboost_exp3"]     # 28 features, in training order
ISO_MIN = CONFIG["iso_normalization"]["min"]
ISO_MAX = CONFIG["iso_normalization"]["max"]

iso_forest = joblib.load(os.path.join(settings.MODELS_DIR, "iso_compatible.joblib"))

xgb_v2 = xgb.XGBClassifier()
xgb_v2.load_model(os.path.join(settings.MODELS_DIR, "xgb_v2_compatible.json"))

xgb_exp3 = xgb.XGBClassifier()
xgb_exp3.load_model(os.path.join(settings.MODELS_DIR, "xgb_exp3_compatible.json"))

with open(settings.ACCOUNT_TYPE_MAP_PATH) as f:
    ACCOUNT_TYPE_MAP = json.load(f)

TYPE_COLUMNS = ["type_ATM_WITHDRAWAL", "type_CASH_DEPOSIT", "type_NACH", "type_NEFT", "type_UPI"]
ACCT_COLUMNS = ["acct_business", "acct_current", "acct_jan_dhan", "acct_savings", "acct_student"]

RBI_STRUCTURING_MIN = 40000
RBI_STRUCTURING_MAX = 49999
RBI_CTR_THRESHOLD = 1000000
RBI_RTGS_MIN = 200000


# ---------- Feature building ----------

def _get_account_type(account_id: str) -> str:
    """
    Static graph se aaya account, toh uska real type map mein milega.
    Naya/unknown account → 'savings' default (India mein sabse common type,
    isliye least-biased guess).
    """
    return ACCOUNT_TYPE_MAP.get(account_id, "savings")


def _get_velocity(account_id: str):
    """
    Persisted DB se is account ki ab tak ki transactions count/sum.
    Naya account → 0, 0 (cold start — expected, not an error).
    """
    history = get_account_history(account_id)
    sent = [h for h in history if h["nameOrig"] == account_id]
    received = [h for h in history if h["nameDest"] == account_id]
    return {
        "sender_txn_count": len(sent),
        "sender_total_amount": sum(h["amount_inr"] for h in sent),
        "receiver_txn_count": len(received),
        "receiver_total_amount": sum(h["amount_inr"] for h in received),
    }


def build_features(txn: dict) -> pd.DataFrame:
    """
    txn expected keys:
      nameOrig, nameDest, amount_inr, oldbalance_inr, newbalance_inr,
      hour (0-23), day_of_week (0-6), txn_type (one of ATM_WITHDRAWAL/
      CASH_DEPOSIT/NACH/NEFT/UPI)
    """
    amount_inr = float(txn["amount_inr"])
    oldbalance_inr = float(txn["oldbalance_inr"])
    newbalance_inr = float(txn["newbalance_inr"])
    hour = int(txn["hour"])
    day_of_week = int(txn["day_of_week"])
    txn_type = txn["txn_type"]

    row = {}

    # Time features
    row["hour"] = hour
    row["day_of_week"] = day_of_week
    row["is_weekend"] = int(day_of_week in [5, 6])
    row["is_odd_hour"] = int(hour in range(0, 6))

    # Amount features
    row["amount_inr"] = amount_inr
    row["amount_log"] = np.log1p(amount_inr)
    if oldbalance_inr <= 0:
        row["amount_to_balance"] = -1
    else:
        row["amount_to_balance"] = round(min(amount_inr / oldbalance_inr, 2.0), 4)
    row["balance_after_zero"] = int(newbalance_inr == 0)

    # Balance features
    row["oldbalance_inr"] = oldbalance_inr
    row["newbalance_inr"] = newbalance_inr
    # Training mein raw PaySim units (amount, oldbalanceOrg, newbalanceOrig) se bana tha.
    # Naya input hamesha INR context mein aayega, isliye INR values pe hi mismatch check.
    balance_mismatch = abs((oldbalance_inr - amount_inr) - newbalance_inr)
    row["balance_mismatch"] = round(balance_mismatch, 2)
    row["has_mismatch"] = int(balance_mismatch > 0.01)

    # RBI threshold features
    row["is_structuring"] = int(RBI_STRUCTURING_MIN <= amount_inr <= RBI_STRUCTURING_MAX)
    row["is_ctr_threshold"] = int(amount_inr >= RBI_CTR_THRESHOLD)
    row["is_rtgs_range"] = int(amount_inr >= RBI_RTGS_MIN)

    # Velocity features (from persisted DB history)
    row.update(_get_velocity(txn["nameOrig"]))
    receiver_velocity = _get_velocity(txn["nameDest"])
    row["receiver_txn_count"] = receiver_velocity["receiver_txn_count"]
    row["receiver_total_amount"] = receiver_velocity["receiver_total_amount"]

    # One-hot: transaction type
    for col in TYPE_COLUMNS:
        row[col] = 0
    row[f"type_{txn_type}"] = 1

    # One-hot: account type (of the sender)
    account_type = _get_account_type(txn["nameOrig"])
    for col in ACCT_COLUMNS:
        row[col] = 0
    row[f"acct_{account_type}"] = 1

    # Domain feature: profile mismatch
    row["is_profile_mismatch"] = int(
        account_type in ["student", "jan_dhan"] and row["is_rtgs_range"] == 1
    )

    return pd.DataFrame([row])


# ---------- Prediction ----------

def predict_ml(txn: dict) -> dict:
    features_full = build_features(txn)

    # XGBoost V2 + Isolation Forest — full 30-feature set, training order se
    X_full = features_full[FEATURES_XGB2]

    iso_raw = -iso_forest.decision_function(X_full)[0]   # higher = more suspicious
    iso_score_01 = (iso_raw - ISO_MIN) / (ISO_MAX - ISO_MIN)
    iso_score_01 = float(np.clip(iso_score_01, 0, 1))

    xgb2_score = float(xgb_v2.predict_proba(X_full)[0][1])

    # XGBoost Exp3 — 28-feature set (type_UPI + has_mismatch dropped)
    X_exp3 = features_full[FEATURES_EXP3]
    exp3_score = float(xgb_exp3.predict_proba(X_exp3)[0][1])

    ml_score = (
        WEIGHTS["isolation_forest"] * iso_score_01 +
        WEIGHTS["xgboost_v2"] * xgb2_score +
        WEIGHTS["xgboost_exp3"] * exp3_score
    )

    if ml_score >= THRESHOLDS["fraud"]:
        risk_level = "FRAUD"
    elif ml_score >= THRESHOLDS["review"]:
        risk_level = "REVIEW"
    else:
        risk_level = "NORMAL"

    return {
        "ml_score": round(ml_score, 4),
        "iso_score": round(iso_score_01, 4),
        "xgb2_score": round(xgb2_score, 4),
        "exp3_score": round(exp3_score, 4),
        "risk_level": risk_level,
    }