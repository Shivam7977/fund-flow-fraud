"""Graph patterns and the 24-hour window. No database: history lookups are faked."""
import pytest
from core import graph_engine as ge


@pytest.fixture(autouse=True)
def _no_saved_history(monkeypatch):
    monkeypatch.setattr(ge, "get_account_history", lambda account_id, user_id=None: [])


def txn(orig, dest, amount, day=10, hour=0):
    return {"nameOrig": orig, "nameDest": dest, "amount_inr": amount, "day": day, "hour": hour}


def run(new, batch):
    return ge.score_graph(new["nameOrig"], new_txn=new, batch_txns=batch)


def patterns(result):
    return {f["pattern"] for f in result["flags"]}


# ---------- structuring ----------

def test_structuring_three_payments_in_24h_is_flagged():
    batch = [txn("S", "R1", 45000, hour=9), txn("S", "R2", 46000, hour=12)]
    res = run(txn("S", "R3", 47000, hour=15), batch)
    assert "structuring" in patterns(res)
    assert res["graph_score"] == 0.8
    assert res["flags"][0]["window_hours"] == 24


def test_structuring_spread_over_months_is_not_flagged():
    batch = [txn("S", "R1", 45000, day=1), txn("S", "R2", 46000, day=40)]
    res = run(txn("S", "R3", 47000, day=80), batch)
    assert "structuring" not in patterns(res)


def test_structuring_zone_boundaries():
    inside = [txn("S", "R1", 40000, hour=1), txn("S", "R2", 49999, hour=2)]
    assert "structuring" in patterns(run(txn("S", "R3", 45000, hour=3), inside))
    outside = [txn("S", "R1", 39999, hour=1), txn("S", "R2", 50000, hour=2)]
    assert "structuring" not in patterns(run(txn("S", "R3", 45000, hour=3), outside))


def test_window_is_strictly_less_than_24_hours():
    # hours 0 and 12 on day 10, and hour 0 on day 11 (= exactly 24h after the first one)
    batch = [txn("S", "R1", 45000, day=10, hour=0), txn("S", "R2", 45000, day=10, hour=12)]
    assert "structuring" not in patterns(run(txn("S", "R3", 45000, day=11, hour=0), batch))
    assert "structuring" in patterns(run(txn("S", "R3", 45000, day=10, hour=23), batch))


# ---------- fan-out / fan-in ----------

def test_fan_out_ten_receivers_within_24h():
    batch = [txn("HUB", f"R{i}", 1000, hour=i) for i in range(9)]
    res = run(txn("HUB", "R9", 1000, hour=10), batch)
    assert "fan_out" in patterns(res)
    assert res["graph_score"] == 0.7


def test_fan_out_nine_receivers_is_not_enough():
    batch = [txn("HUB", f"R{i}", 1000, hour=i) for i in range(8)]
    assert "fan_out" not in patterns(run(txn("HUB", "R8", 1000, hour=9), batch))


def test_fan_out_spread_over_days_is_not_flagged():
    batch = [txn("HUB", f"R{i}", 1000, day=10 + i) for i in range(9)]
    assert "fan_out" not in patterns(run(txn("HUB", "R9", 1000, day=19), batch))


def test_fan_in_is_detected_on_the_receiver_even_if_sender_is_new():
    batch = [txn(f"S{i}", "MULE", 2000, hour=i) for i in range(9)]
    res = run(txn("S9", "MULE", 2000, hour=10), batch)
    fan_in = [f for f in res["flags"] if f["pattern"] == "fan_in"]
    assert fan_in and fan_in[0]["unique_senders"] == 10
    assert res["has_history"] is True   # a flag counts as history so the score is blended


# ---------- round trip ----------

def test_round_trip_three_hop_cycle_inside_window():
    batch = [txn("A", "B", 5000, hour=9), txn("B", "C", 5000, hour=10)]
    res = run(txn("C", "A", 5000, hour=11), batch)
    assert "round_trip" in patterns(res)
    assert res["graph_score"] == 0.9


def test_round_trip_with_edges_days_apart_is_not_flagged():
    batch = [txn("A", "B", 5000, day=1), txn("B", "C", 5000, day=3)]
    assert "round_trip" not in patterns(run(txn("C", "A", 5000, day=5), batch))


def test_no_cycle_no_flag():
    batch = [txn("A", "B", 5000, hour=9), txn("B", "C", 5000, hour=10)]
    assert "round_trip" not in patterns(run(txn("C", "D", 5000, hour=11), batch))


# ---------- score / history semantics ----------

def test_graph_score_is_the_strongest_pattern_not_an_average():
    batch = [txn("S", f"R{i}", 45000 if i < 3 else 1000, hour=i) for i in range(11)]
    res = run(txn("S", "R11", 1000, hour=12), batch)
    assert {"structuring", "fan_out"} <= patterns(res)
    assert res["graph_score"] == 0.8      # max(0.8, 0.7), not 0.75


def test_account_with_no_history_gets_no_sender_side_patterns():
    res = run(txn("NEW", "R1", 45000), [])
    assert res == {"graph_score": 0.0, "flags": [], "has_history": False}


def test_history_lookup_is_scoped_to_the_given_user(monkeypatch):
    calls = []
    monkeypatch.setattr(ge, "get_account_history", lambda a, u=None: calls.append((a, u)) or [])
    ge.score_graph("S", new_txn=txn("S", "R", 100), user_id=7)
    assert calls and all(u == 7 for _, u in calls)      # sender and receiver lookups
    calls.clear()
    ge.score_graph("S", new_txn=txn("S", "R", 100))      # guest / anonymous
    assert calls and all(u is None for _, u in calls)


def test_saved_history_of_the_same_user_counts(monkeypatch):
    saved = [{"nameOrig": "S", "nameDest": f"R{i}", "amount_inr": 45000, "day": 10, "hour": 9 + i} for i in range(2)]
    monkeypatch.setattr(ge, "get_account_history", lambda a, u=None: saved if (u == 1 and a == "S") else [])
    new = txn("S", "R9", 45000, hour=14)
    assert "structuring" in patterns(ge.score_graph("S", new_txn=new, user_id=1))
    assert "structuring" not in patterns(ge.score_graph("S", new_txn=new, user_id=2))
    assert "structuring" not in patterns(ge.score_graph("S", new_txn=new, user_id=None))
