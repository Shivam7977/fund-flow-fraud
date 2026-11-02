"""CSV column matching and time-field handling."""
import pandas as pd
import pytest
from core.csv_mapper import analyze_columns, resolve_time_fields, SOFT_DEFAULTS


def test_standard_columns_are_found_case_space_and_underscore_insensitive():
    cols = ["Sender", "RECEIVER ", "Amount", "txn-type", "Old_Balance", "newBalance", "HOUR", "Day Of Week", "day"]
    a = analyze_columns(cols)
    assert a["missing_hard"] == [] and a["missing_soft"] == []
    assert a["mapping"]["nameOrig"] == "Sender"
    assert a["mapping"]["nameDest"] == "RECEIVER "
    assert a["mapping"]["txn_type"] == "txn-type"
    assert a["mapping"]["day_of_week"] == "Day Of Week"


def test_missing_required_columns_are_reported():
    a = analyze_columns(["sender", "amount"])
    assert set(a["missing_hard"]) == {"nameDest", "txn_type"}


def test_missing_optional_columns_need_confirmation():
    a = analyze_columns(["sender", "receiver", "amount", "type"])
    assert set(a["missing_soft"]) == {"oldbalance_inr", "newbalance_inr", "hour", "day_of_week", "day"}


def test_timestamp_column_covers_hour_weekday_and_day():
    a = analyze_columns(["sender", "receiver", "amount", "type", "oldbalance", "newbalance", "timestamp"])
    assert a["missing_soft"] == []
    assert a["mapping"]["timestamp"] == "timestamp"


def test_manual_mapping_wins():
    a = analyze_columns(["from", "to", "amt", "kind"], manual_mapping={"txn_type": "kind", "nameOrig": "from"})
    assert a["mapping"]["txn_type"] == "kind" and a["mapping"]["nameOrig"] == "from"


def test_resolve_from_timestamp():
    row = pd.Series({"ts": "2026-10-15 10:05:00"})  # a Thursday
    hour, dow, day = resolve_time_fields(row, {"timestamp": "ts"})
    assert (hour, dow) == (10, 3)
    assert day == int(pd.Timestamp("2026-10-15 10:05:00").timestamp() // 86400)


def test_date_only_timestamp_means_hour_zero_and_same_day():
    h1, _, d1 = resolve_time_fields(pd.Series({"ts": "2026-10-15"}), {"timestamp": "ts"})
    h2, _, d2 = resolve_time_fields(pd.Series({"ts": "2026-10-15"}), {"timestamp": "ts"})
    assert h1 == 0 and d1 == d2


def test_explicit_columns_beat_the_timestamp():
    row = pd.Series({"h": 7, "ts": "2026-10-15 10:05:00"})
    hour, _, _ = resolve_time_fields(row, {"hour": "h", "timestamp": "ts"})
    assert hour == 7


def test_unparseable_or_missing_time_falls_back_to_defaults():
    hour, dow, day = resolve_time_fields(pd.Series({"ts": "not a date"}), {"timestamp": "ts"})
    assert (hour, dow, day) == (SOFT_DEFAULTS["hour"], SOFT_DEFAULTS["day_of_week"], SOFT_DEFAULTS["day"])
    assert resolve_time_fields(pd.Series({}), {}) == (0, 0, 0)
