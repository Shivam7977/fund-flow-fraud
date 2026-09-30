"""
CSV column mapping helper.

MANDATORY fields (HARD_REQUIRED) — inke bina prediction ka koi matlab nahi,
CSV mein kisi bhi naam se hona hi chahiye, warna job fail hoga.

OPTIONAL fields (SOFT_REQUIRED) — na milein to safe default value use hogi,
but processing se PEHLE user ko confirm karna padega (silent assumption nahi).

TIMESTAMP fallback — real-world CSVs mein aksar hour/day_of_week alag columns
ki tarah nahi, ek hi poore timestamp/datetime column mein hote hain (e.g.
"2026-09-10 02:10:00"). Agar aisa column mile, to hour/day_of_week/day
seedha usी se derive ho jaate hain — SOFT_DEFAULTS (0/Monday) tabhi lagते
hain jab na alag column mile, na timestamp se derive ho paaye.
"""

import pandas as pd

ALIASES = {
    "nameOrig": ["nameorig", "name_orig", "sender", "sender_id", "from_account",
                 "orig", "origin", "source_account", "account_from"],
    "nameDest": ["namedest", "name_dest", "receiver", "receiver_id", "to_account",
                 "dest", "destination", "target_account", "account_to"],
    "amount_inr": ["amount", "amt", "amount_inr", "transaction_amount", "txn_amount", "value"],
    "oldbalance_inr": ["oldbalance", "oldbalanceorg", "old_balance", "balance_before", "opening_balance"],
    "newbalance_inr": ["newbalance", "newbalanceorig", "new_balance", "balance_after", "closing_balance"],
    "hour": ["hour", "txn_hour", "time_hour"],
    "day_of_week": ["day_of_week", "dow", "weekday"],
    "day": ["day", "txn_day", "day_number"],
    "txn_type": ["type", "txn_type", "transaction_type", "mode"],
    # Yeh HARD_REQUIRED/SOFT_REQUIRED mein nahi hai — sirf hour/day_of_week/day
    # derive karne ke liye ek auxiliary source hai, khud koi model feature nahi.
    "timestamp": ["timestamp", "datetime", "date_time", "txn_time", "transaction_time",
                  "txn_timestamp", "created_at", "time"],
}

HARD_REQUIRED = ["nameOrig", "nameDest", "amount_inr", "txn_type"]
SOFT_REQUIRED = ["oldbalance_inr", "newbalance_inr", "hour", "day_of_week", "day"]

SOFT_DEFAULTS = {
    "oldbalance_inr": 0.0,
    "newbalance_inr": 0.0,
    "hour": 0,
    "day_of_week": 0,
    "day": 0,
}

# hour/day_of_week/day — agar timestamp column mile to inhe derive kiya ja
# sakta hai, isliye timestamp maujood hone par inhe missing_soft mein
# confirmation ke liye mat maango.
_DERIVABLE_FROM_TIMESTAMP = {"hour", "day_of_week", "day"}


def _normalize(col: str) -> str:
    """Case/space/underscore-insensitive matching ke liye."""
    return str(col).strip().lower().replace(" ", "").replace("-", "").replace("_", "")


def analyze_columns(csv_columns: list, manual_mapping: dict = None) -> dict:
    """
    csv_columns: uploaded CSV ke actual column names
    manual_mapping: agar user ne pehle se field->column mapping di hai (confirm step se), usko priority milegi

    Returns:
        {
            "mapping": {field: actual_csv_column_or_None, ...},   # "timestamp" bhi ho sakta hai
            "missing_hard": [...],   # inke bina job fail hoga
            "missing_soft": [...],   # inke bina confirmation chahiye
        }
    """
    manual_mapping = manual_mapping or {}
    normalized_lookup = {_normalize(c): c for c in csv_columns}

    mapping = {}
    for field, aliases in ALIASES.items():
        if field in manual_mapping and manual_mapping[field]:
            mapping[field] = manual_mapping[field]
            continue

        found = None
        for candidate in [field] + aliases:
            norm_candidate = _normalize(candidate)
            if norm_candidate in normalized_lookup:
                found = normalized_lookup[norm_candidate]
                break
        mapping[field] = found

    missing_hard = [f for f in HARD_REQUIRED if not mapping.get(f)]
    missing_soft = [f for f in SOFT_REQUIRED if not mapping.get(f)]

    # timestamp column mila to hour/day_of_week/day ke liye confirmation mat maango —
    # yeh resolve_time_fields() se derive ho jaayenge.
    if mapping.get("timestamp"):
        missing_soft = [f for f in missing_soft if f not in _DERIVABLE_FROM_TIMESTAMP]

    return {"mapping": mapping, "missing_hard": missing_hard, "missing_soft": missing_soft}


def resolve_time_fields(row, mapping: dict) -> tuple[int, int, int]:
    """
    hour, day_of_week, day nikalta hai ek CSV row se — priority order:
      1. Explicit column (mapping["hour"] / ["day_of_week"] / ["day"]) agar mili
      2. Warna ek poora timestamp/datetime column mila to usse derive
      3. Warna SOFT_DEFAULTS (0 / Monday / 0) — sirf tab jab dono na milein

    day_of_week: Monday=0 ... Sunday=6 (frontend ke computeDayFields() jaisa hi,
    taaki single-check aur CSV-batch paths consistent rahein).
    day: epoch day number (Unix days since 1970-01-01), grouping/velocity ke liye
    ek stable reference — frontend ke Math.floor(timestamp_ms / 86400000) jaisa.
    """
    hour = day_of_week = day = None

    if mapping.get("hour"):
        try:
            hour = int(row[mapping["hour"]])
        except (TypeError, ValueError):
            hour = None

    if mapping.get("day_of_week"):
        try:
            day_of_week = int(row[mapping["day_of_week"]])
        except (TypeError, ValueError):
            day_of_week = None

    if mapping.get("day"):
        try:
            day = int(row[mapping["day"]])
        except (TypeError, ValueError):
            day = None

    if (hour is None or day_of_week is None or day is None) and mapping.get("timestamp"):
        try:
            ts = pd.to_datetime(row[mapping["timestamp"]])
            if pd.isna(ts):
                raise ValueError("unparseable timestamp")
            if hour is None:
                hour = int(ts.hour)
            if day_of_week is None:
                day_of_week = int(ts.weekday())
            if day is None:
                day = int(ts.timestamp() // 86400)
        except (TypeError, ValueError, OverflowError):
            pass  # dono se nahi mila — neeche defaults lag jaayenge

    if hour is None:
        hour = SOFT_DEFAULTS["hour"]
    if day_of_week is None:
        day_of_week = SOFT_DEFAULTS["day_of_week"]
    if day is None:
        day = SOFT_DEFAULTS["day"]

    return hour, day_of_week, day