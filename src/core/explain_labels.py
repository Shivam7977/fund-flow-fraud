"""
Feature name + value -> plain-English reason.

Ye file sirf text banati hai (koi model / DB import nahi), isliye ml_engine.py
ise safely import kar sakta hai bina circular import ke.
Saari strings user ko dikhti hain, isliye English mein hain.
"""

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _inr(v) -> str:
    return f"INR {float(v):,.0f}"


def _onehot(prefix_text: str, label: str):
    """One-hot columns ke liye: value 1 ho to 'is X', 0 ho to 'is not X'."""
    def fn(v):
        return f"{prefix_text} is {label}" if float(v) >= 0.5 else f"{prefix_text} is not {label}"
    return fn


def _amount_to_balance(v) -> str:
    v = float(v)
    if v < 0:
        # ml_engine.build_features: oldbalance <= 0 par -1 set hota hai
        return "Sender's balance is unknown or zero, so the amount cannot be compared with it"
    return f"Amount is {v * 100:.0f}% of the sender's balance"


FEATURE_LABELS = {
    # Time
    "hour": lambda v: f"Transaction made at {int(float(v)):02d}:00",
    "day_of_week": lambda v: f"Transaction made on a {_DAYS[int(float(v)) % 7]}",
    "is_weekend": lambda v: "Made on a weekend" if float(v) >= 0.5 else "Made on a weekday",
    "is_odd_hour": lambda v: "Made in odd hours (12am to 6am)" if float(v) >= 0.5 else "Made during normal hours",

    # Amount
    "amount_inr": lambda v: f"Transaction amount is {_inr(v)}",
    "amount_log": lambda v: "Overall size of the transaction amount",
    "amount_to_balance": _amount_to_balance,
    "balance_after_zero": lambda v: (
        "Sender's balance drops to zero after this transaction"
        if float(v) >= 0.5 else "Sender keeps some balance after this transaction"
    ),

    # Balances
    "oldbalance_inr": lambda v: f"Sender's balance before is {_inr(v)}",
    "newbalance_inr": lambda v: f"Sender's balance after is {_inr(v)}",
    "balance_mismatch": lambda v: (
        f"Balance change differs from the amount by {_inr(v)}"
        if float(v) > 0.01 else "Balance change matches the amount exactly"
    ),
    "has_mismatch": lambda v: (
        "Balances do not add up with the amount" if float(v) >= 0.5 else "Balances add up with the amount"
    ),

    # RBI thresholds
    "is_structuring": lambda v: (
        "Amount is in the INR 40,000-49,999 band just below the reporting limit (possible structuring)"
        if float(v) >= 0.5 else "Amount is outside the structuring band"
    ),
    "is_ctr_threshold": lambda v: (
        "Amount is at or above INR 10 lakh (cash transaction reporting level)"
        if float(v) >= 0.5 else "Amount is below the INR 10 lakh reporting level"
    ),
    "is_rtgs_range": lambda v: (
        "Amount is in RTGS range (INR 2 lakh or more)" if float(v) >= 0.5 else "Amount is below RTGS range"
    ),

    # Velocity
    "sender_txn_count": lambda v: f"Sender has {int(float(v))} earlier transactions on record",
    "sender_total_amount": lambda v: f"Sender's earlier transactions total {_inr(v)}",
    "receiver_txn_count": lambda v: f"Receiver has received {int(float(v))} earlier transactions",
    "receiver_total_amount": lambda v: f"Receiver's earlier incoming transactions total {_inr(v)}",

    # Transaction type (one-hot)
    "type_ATM_WITHDRAWAL": _onehot("Transaction type", "ATM withdrawal"),
    "type_CASH_DEPOSIT": _onehot("Transaction type", "cash deposit"),
    "type_NACH": _onehot("Transaction type", "NACH"),
    "type_NEFT": _onehot("Transaction type", "NEFT"),
    "type_UPI": _onehot("Transaction type", "UPI"),

    # Sender account type (one-hot)
    "acct_business": _onehot("Sender account type", "business"),
    "acct_current": _onehot("Sender account type", "current"),
    "acct_jan_dhan": _onehot("Sender account type", "Jan Dhan"),
    "acct_savings": _onehot("Sender account type", "savings"),
    "acct_student": _onehot("Sender account type", "student"),

    # Domain feature
    "is_profile_mismatch": lambda v: (
        "A student or Jan Dhan account is making an RTGS-size transfer"
        if float(v) >= 0.5 else "Amount fits the sender's account profile"
    ),
}


def describe_feature(name: str, value) -> str:
    """Kisi bhi feature ke liye reason text. Unknown feature aaye to crash nahi, raw naam dikhao."""
    fn = FEATURE_LABELS.get(name)
    if fn is None:
        return f"{name} = {value}"
    try:
        return fn(value)
    except (TypeError, ValueError):
        return f"{name} = {value}"