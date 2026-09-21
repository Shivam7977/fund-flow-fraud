"""
Step 1 ka quick check. `src` folder se chalao:  python test_explain.py
DB ki zaroorat nahi — get_account_history ko stub kar diya hai.
"""
import json
import numpy as np
import xgboost as xgb
from core import ml_engine as m

m.get_account_history = lambda account_id: []   # DB ke bina test

txn = {
    "nameOrig": "TEST-1", "nameDest": "TEST-2", "amount_inr": 48750,
    "oldbalance_inr": 49000, "newbalance_inr": 0,
    "hour": 2, "day_of_week": 5, "txn_type": "NEFT",
}

features = m.build_features(txn)

# SHAP values sahi hain ya nahi: contributions ka sum sigmoid karke predict_proba se match hona chahiye
for name, model, cols in [("xgb_v2", m.xgb_v2, m.FEATURES_XGB2), ("xgb_exp3", m.xgb_exp3, m.FEATURES_EXP3)]:
    X = features[cols]
    raw = model.get_booster().predict(xgb.DMatrix(X), pred_contribs=True)[0]
    from_shap = 1 / (1 + np.exp(-raw.sum()))
    from_model = float(model.predict_proba(X)[0][1])
    print(f"{name}: SHAP-based probability={from_shap:.6f}, model probability={from_model:.6f}")
    assert abs(from_shap - from_model) < 1e-4, f"SHAP mismatch for {name}"

result = m.predict_ml(txn, explain=True)
print(json.dumps(result, indent=2, ensure_ascii=False))
print("All checks passed.")