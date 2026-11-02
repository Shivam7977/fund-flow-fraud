"""Smoke tests on the real trained ensemble (the demo transactions from the README)."""
import pytest
from core.ml_engine import predict_ml, THRESHOLDS
from routes.predict import _combine

DRAIN = dict(nameOrig="ACC-7001", nameDest="ACC-7002", amount_inr=185000.0, txn_type="NEFT",
             oldbalance_inr=185000.0, newbalance_inr=0.0, hour=2, day_of_week=2, day=20000)
NORMAL = dict(nameOrig="ACC-7005", nameDest="MERCHANT-GROCERY", amount_inr=1240.0, txn_type="UPI",
              oldbalance_inr=58200.0, newbalance_inr=56960.0, hour=11, day_of_week=2, day=20000)


def test_account_drain_is_fraud():
    ml = predict_ml(DRAIN, explain=True)
    assert 0.0 <= ml["ml_score"] <= 1.0
    score, risk = _combine(ml["ml_score"], 0.0, has_history=False)
    assert risk == "FRAUD" and score >= THRESHOLDS["fraud"]


def test_everyday_upi_payment_is_normal():
    ml = predict_ml(NORMAL)
    assert _combine(ml["ml_score"], 0.0, has_history=False)[1] == "NORMAL"


def test_scores_for_every_model_are_exposed():
    ml = predict_ml(DRAIN)
    for key in ("ml_score", "iso_score", "xgb2_score", "exp3_score"):
        assert 0.0 <= ml[key] <= 1.0


def test_explanation_has_at_most_five_plain_english_reasons():
    ex = predict_ml(DRAIN, explain=True)["explanation"]
    items = ex["top_features"]
    assert 1 <= len(items) <= 5
    for it in items:
        assert it["direction"] in ("fraud", "normal") and isinstance(it["reason"], str) and it["reason"]
    assert any("100% of the sender's balance" in it["reason"] for it in items)


def test_a_transaction_with_an_unknown_type_does_not_crash():
    # the single-transaction API only accepts 5 types, but a CSV can contain anything
    ml = predict_ml(dict(NORMAL, txn_type="TRANSFER"))
    assert 0.0 <= ml["ml_score"] <= 1.0
