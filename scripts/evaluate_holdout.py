"""
Hold-out evaluation of the saved ensemble.
Nothing is re-trained and nothing in models/ is modified.

Problem it addresses: the ensemble weights, the Isolation Forest normalisation range
and the fraud threshold in models/ensemble_config.json were all derived on the same
20% test set that the README reports on, so the reported precision/recall/F1 are
optimistic.

What it does:
  1. Reproduces the original split (test_size=0.2, random_state=42, stratified).
  2. Splits that test set 50/50 (stratified, fixed seed) into "tune" and "holdout".
  3. Re-derives the Isolation Forest range, the AUC-proportional weights and the
     F1-optimal threshold on "tune" only, then reports metrics on "holdout", which
     none of those choices have seen.
  4. Does the same for XGBoost V2 alone, Exp3 alone and V2+Exp3 averaged, so you can
     see whether the Isolation Forest actually helps.

Usage (from the repo root):
    python scripts/evaluate_holdout.py --data path/to/model_features.csv

Remaining caveat: the XGBoost hyperparameters and feature choices (for example
dropping type_UPI for Exp3) were made while looking at the full test set, so even
the hold-out half is mildly optimistic. A fully clean estimate needs a
train/validation/test split and a re-train.
"""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score
from sklearn.model_selection import train_test_split

BASE = Path(__file__).resolve().parent.parent
HOLDOUT_SEED = 0
N_BOOTSTRAP = 200

CAVEAT = (
    "Weights, ISO range and thresholds were re-derived on the 'tune' half of the original "
    "test set and metrics are reported on the 'holdout' half. Model hyperparameters and "
    "feature choices were still made while looking at the full test set, so these numbers "
    "are mildly optimistic."
)


def f1_optimal_threshold(y, score):
    p, r, thr = precision_recall_curve(y, score)
    f1 = 2 * p * r / (p + r + 1e-12)
    return float(thr[np.argmax(f1[:-1])])  # the last curve point has no threshold


def confusion(y, pred):
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    return tp, fp, fn, tn


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def bootstrap_ci(y, pred, n=N_BOOTSTRAP, seed=0):
    rng = np.random.default_rng(seed)
    vals = {"precision": [], "recall": [], "f1": []}
    for _ in range(n):
        idx = rng.integers(0, len(y), len(y))
        tp, fp, fn, _ = confusion(y[idx], pred[idx])
        p, r, f = prf(tp, fp, fn)
        vals["precision"].append(p)
        vals["recall"].append(r)
        vals["f1"].append(f)
    return {k: [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]
            for k, v in vals.items()}


def evaluate(name, y_tune, s_tune, y_hold, s_hold, threshold=None):
    """Threshold is F1-optimal on the tune half unless one is given (shipped config)."""
    thr = f1_optimal_threshold(y_tune, s_tune) if threshold is None else float(threshold)
    pred = (s_hold >= thr).astype(int)
    tp, fp, fn, tn = confusion(y_hold, pred)
    p, r, f = prf(tp, fp, fn)
    return {
        "model": name,
        "pr_auc": round(float(average_precision_score(y_hold, s_hold)), 4),
        "roc_auc": round(float(roc_auc_score(y_hold, s_hold)), 4),
        "threshold": round(thr, 4),
        "precision": round(p, 4),
        "recall": round(r, 4),
        "f1": round(f, 4),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "ci_95": bootstrap_ci(y_hold, pred),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="path to model_features.csv")
    ap.add_argument("--models-dir", default=str(BASE / "models"))
    ap.add_argument("--out", default=str(BASE / "docs" / "holdout_evaluation.json"))
    args = ap.parse_args()
    models = Path(args.models_dir)

    cfg = json.loads((models / "ensemble_config.json").read_text())
    f_v2, f_exp3 = cfg["features"]["xgboost_v2"], cfg["features"]["xgboost_exp3"]
    cfg_w, cfg_thr, cfg_norm = cfg["weights"], cfg["thresholds"], cfg["iso_normalization"]

    # Only the 24 model features + target are needed; the 6 dropped columns are skipped.
    df = pd.read_csv(args.data, usecols=f_v2 + ["isFraud"])
    y_all = df["isFraud"].to_numpy()
    # Same split as notebooks/02_model_training.ipynb: it depends only on row count and labels.
    _, test_idx = train_test_split(np.arange(len(df)), test_size=0.2, random_state=42, stratify=y_all)
    X_test = df.iloc[test_idx][f_v2]
    y_test = y_all[test_idx]
    del df

    iso = joblib.load(models / "iso_compatible.joblib")
    v2 = xgb.XGBClassifier()
    v2.load_model(str(models / "xgb_v2_compatible.json"))
    exp3 = xgb.XGBClassifier()
    exp3.load_model(str(models / "xgb_exp3_compatible.json"))

    iso_raw = -iso.decision_function(X_test)  # higher = more suspicious
    p_v2 = v2.predict_proba(X_test)[:, 1]
    p_exp3 = exp3.predict_proba(X_test[f_exp3])[:, 1]
    del X_test

    tune_i, hold_i = train_test_split(
        np.arange(len(y_test)), test_size=0.5, random_state=HOLDOUT_SEED, stratify=y_test
    )
    yt, yh = y_test[tune_i], y_test[hold_i]

    # ---- everything below is derived on the tune half only ----
    lo, hi = float(iso_raw[tune_i].min()), float(iso_raw[tune_i].max())
    s_iso = np.clip((iso_raw - lo) / (hi - lo), 0, 1)
    aucs = {
        "isolation_forest": roc_auc_score(yt, s_iso[tune_i]),
        "xgboost_v2": roc_auc_score(yt, p_v2[tune_i]),
        "xgboost_exp3": roc_auc_score(yt, p_exp3[tune_i]),
    }
    total = sum(aucs.values())
    w = {k: v / total for k, v in aucs.items()}  # same AUC-proportional rule as the notebook
    ens = w["isolation_forest"] * s_iso + w["xgboost_v2"] * p_v2 + w["xgboost_exp3"] * p_exp3

    # Reference: the shipped config applied as-is (its values came from the full test set).
    s_iso_cfg = np.clip((iso_raw - cfg_norm["min"]) / (cfg_norm["max"] - cfg_norm["min"]), 0, 1)
    ens_cfg = (cfg_w["isolation_forest"] * s_iso_cfg + cfg_w["xgboost_v2"] * p_v2
               + cfg_w["xgboost_exp3"] * p_exp3)

    candidates = [
        ("xgboost_v2 alone", p_v2, None),
        ("xgboost_exp3 alone", p_exp3, None),
        ("xgboost v2+exp3 average", (p_v2 + p_exp3) / 2, None),
        ("ensemble re-tuned on tune half", ens, None),
        ("ensemble shipped config (optimistic ref.)", ens_cfg, cfg_thr["fraud"]),
    ]
    rows = [evaluate(n, yt, s[tune_i], yh, s[hold_i], thr) for n, s, thr in candidates]

    table = pd.DataFrame([{k: r[k] for k in
                           ("model", "pr_auc", "roc_auc", "threshold", "precision", "recall", "f1", "fp", "fn")}
                          for r in rows])
    print(f"tune half: {len(yt):,} rows, {int(yt.sum())} fraud | "
          f"holdout half: {len(yh):,} rows, {int(yh.sum())} fraud\n")
    print(table.to_string(index=False))
    print("\n95% bootstrap intervals on the holdout half:")
    for r in rows:
        ci = r["ci_95"]
        print(f"  {r['model']:<45} precision {ci['precision']}  recall {ci['recall']}  f1 {ci['f1']}")
    print("\nDerived on the tune half:")
    print("  weights:", {k: round(v, 4) for k, v in w.items()})
    print("  iso range:", [round(lo, 4), round(hi, 4)], "(shipped:", [cfg_norm["min"], cfg_norm["max"]], ")")
    print("\nNOTE:", CAVEAT)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "n_tune": int(len(yt)), "n_tune_fraud": int(yt.sum()),
        "n_holdout": int(len(yh)), "n_holdout_fraud": int(yh.sum()),
        "holdout_seed": HOLDOUT_SEED,
        "derived_on_tune": {"weights": {k: round(v, 4) for k, v in w.items()},
                            "iso_range": [round(lo, 4), round(hi, 4)]},
        "results": rows,
        "caveat": CAVEAT,
    }, indent=2))
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main()