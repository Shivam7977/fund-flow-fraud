"""
Reproducible evaluation of the saved ensemble.

Re-computes the headline metrics from the models in models/ using the exact
split from notebooks/02_model_training.ipynb (test_size=0.2, random_state=42,
stratified). Nothing is re-fitted: weights, thresholds and the Isolation Forest
normalisation range are read from models/ensemble_config.json.

Usage (from the repo root):
    python scripts/evaluate_model.py --data path/to/model_features.csv

model_features.csv is produced by notebooks/01_EDA.ipynb and is NOT in the repo
(it is far too large for GitHub). Writes docs/evaluation_results.json.

Caveat printed with the results: the ensemble weights, the thresholds and the
Isolation Forest normalisation range were all derived from this same test set
(there is no separate validation split), so precision/recall/F1 at the chosen
threshold are optimistic estimates.
"""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
import xgboost as xgb
from sklearn.metrics import (
    average_precision_score, confusion_matrix, f1_score,
    precision_score, recall_score, roc_auc_score,
)
from sklearn.model_selection import train_test_split

BASE = Path(__file__).resolve().parent.parent
MODELS = BASE / "models"
OUT = BASE / "docs" / "evaluation_results.json"

# Same columns notebook 02 drops (balance-mismatch = simulator artifact,
# velocity = future-information leakage).
DROP_COLS = [
    "balance_mismatch", "has_mismatch",
    "sender_txn_count", "sender_total_amount",
    "receiver_txn_count", "receiver_total_amount",
]

# Values printed by notebook 02 (cells 7-9), for a side-by-side check.
NOTEBOOK = {
    "n_test": 1_272_524, "n_fraud": 1_643,
    "auc_isolation_forest": 0.8451, "auc_xgboost_v2": 0.9997, "auc_xgboost_exp3": 0.9997,
    "ensemble_auc_roc": 0.9983,
    "precision": 0.8863, "recall": 0.9014, "f1": 0.8938,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="path to model_features.csv")
    args = ap.parse_args()

    cfg = json.loads((MODELS / "ensemble_config.json").read_text())
    w, thr, norm = cfg["weights"], cfg["thresholds"], cfg["iso_normalization"]
    f_v2, f_exp3 = cfg["features"]["xgboost_v2"], cfg["features"]["xgboost_exp3"]

    df = pd.read_csv(args.data)
    X = df.drop(["isFraud"] + DROP_COLS, axis=1)
    y = df["isFraud"]
    _, X_test, _, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    y_arr = y_test.values

    iso = joblib.load(MODELS / "iso_compatible.joblib")
    xgb_v2 = xgb.XGBClassifier()
    xgb_v2.load_model(str(MODELS / "xgb_v2_compatible.json"))
    xgb_exp3 = xgb.XGBClassifier()
    xgb_exp3.load_model(str(MODELS / "xgb_exp3_compatible.json"))

    iso_raw = -iso.decision_function(X_test[f_v2])
    iso01 = np.clip((iso_raw - norm["min"]) / (norm["max"] - norm["min"]), 0, 1)
    p_v2 = xgb_v2.predict_proba(X_test[f_v2])[:, 1]
    p_exp3 = xgb_exp3.predict_proba(X_test[f_exp3])[:, 1]

    ens = w["isolation_forest"] * iso01 + w["xgboost_v2"] * p_v2 + w["xgboost_exp3"] * p_exp3
    pred = (ens >= thr["fraud"]).astype(int)
    review = int(((ens >= thr["review"]) & (ens < thr["fraud"])).sum())
    tn, fp, fn, tp = confusion_matrix(y_arr, pred).ravel()

    results = {
        "n_test": int(len(y_arr)),
        "n_fraud": int(y_arr.sum()),
        "auc_isolation_forest": round(roc_auc_score(y_arr, iso01), 4),
        "auc_xgboost_v2": round(roc_auc_score(y_arr, p_v2), 4),
        "auc_xgboost_exp3": round(roc_auc_score(y_arr, p_exp3), 4),
        "ensemble_auc_roc": round(roc_auc_score(y_arr, ens), 4),
        "ensemble_pr_auc": round(average_precision_score(y_arr, ens), 4),
        "precision": round(precision_score(y_arr, pred), 4),
        "recall": round(recall_score(y_arr, pred), 4),
        "f1": round(f1_score(y_arr, pred), 4),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "review_band_count": review,
        "thresholds_used": thr,
        "weights_used": w,
        "versions": {"scikit_learn": sklearn.__version__, "xgboost": xgb.__version__},
        "caveat": ("Weights, thresholds and ISO normalisation were derived from this same "
                   "test set (no separate validation split); precision/recall/F1 are optimistic."),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2))

    print(f"{'metric':<22}{'this run':>12}{'notebook 02':>14}")
    for k, nb in NOTEBOOK.items():
        print(f"{k:<22}{results[k]:>12}{nb:>14}")
    print(f"{'ensemble_pr_auc':<22}{results['ensemble_pr_auc']:>12}{'(not in nb)':>14}")
    print(f"\nSaved -> {OUT}")
    print("NOTE:", results["caveat"])


if __name__ == "__main__":
    main()