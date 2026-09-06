"""
CSV column mapping helper.

MANDATORY fields (HARD_REQUIRED) — inke bina prediction ka koi matlab nahi,
CSV mein kisi bhi naam se hona hi chahiye, warna job fail hoga.

OPTIONAL fields (SOFT_REQUIRED) — na milein to safe default value use hogi,
but processing se PEHLE user ko confirm karna padega (silent assumption nahi).
"""

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


def _normalize(col: str) -> str:
    """Case/space/underscore-insensitive matching ke liye."""
    return str(col).strip().lower().replace(" ", "").replace("-", "").replace("_", "")


def analyze_columns(csv_columns: list, manual_mapping: dict = None) -> dict:
    """
    csv_columns: uploaded CSV ke actual column names
    manual_mapping: agar user ne pehle se field->column mapping di hai (confirm step se), usko priority milegi

    Returns:
        {
            "mapping": {field: actual_csv_column_or_None, ...},
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

    return {"mapping": mapping, "missing_hard": missing_hard, "missing_soft": missing_soft}