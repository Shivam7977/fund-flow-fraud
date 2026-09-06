import networkx as nx
import pandas as pd
from core.db import get_account_history

ROUND_TRIP_MAX_LENGTH = 4
FAN_DEGREE_THRESHOLD = 10
STRUCTURING_MIN = 40000
STRUCTURING_MAX = 49999
STRUCTURING_MIN_COUNT = 3

def build_local_subgraph(account_id: str, new_txn: dict = None, batch_txns: list = None) -> tuple:
    history = get_account_history(account_id)

    # batch_txns = isi request ke andar PEHLE process ho chuke transactions
    # (abhi DB mein save nahi hue, but graph detection ke liye count hone chahiye)
    batch_rows = [
        t for t in (batch_txns or [])
        if t["nameOrig"] == account_id or t["nameDest"] == account_id
    ]

    has_history = len(history) > 0 or len(batch_rows) > 0   # 👈 new_txn add hone se PEHLE check karo

    rows = [{
        "nameOrig": h["nameOrig"], "nameDest": h["nameDest"],
        "amount_inr": h["amount_inr"], "day": h["day"], "hour": h["hour"],
    } for h in history]

    rows += [{
        "nameOrig": t["nameOrig"], "nameDest": t["nameDest"],
        "amount_inr": t["amount_inr"], "day": t.get("day", 0), "hour": t.get("hour", 0),
    } for t in batch_rows]

    if new_txn:
        rows.append({
            "nameOrig": new_txn["nameOrig"], "nameDest": new_txn["nameDest"],
            "amount_inr": new_txn["amount_inr"],
            "day": new_txn.get("day", 0), "hour": new_txn.get("hour", 0),
        })

    if not rows:
        return nx.DiGraph(), pd.DataFrame(), has_history

    df_local = pd.DataFrame(rows)
    G_local = nx.from_pandas_edgelist(
        df_local, source="nameOrig", target="nameDest",
        edge_attr=["amount_inr", "day", "hour"], create_using=nx.DiGraph()
    )
    return G_local, df_local, has_history   # 👈 teesra value return karo


def detect_round_trip(G: nx.DiGraph) -> list:
    """A -> B -> C -> A jaise cycles, max 4 hops tak."""
    cycles = []
    try:
        for cycle in nx.simple_cycles(G, length_bound=ROUND_TRIP_MAX_LENGTH):
            if len(cycle) >= 3:
                cycles.append(cycle)
    except Exception:
        pass
    return cycles


def detect_fan_out(df_local: pd.DataFrame, account_id: str) -> dict | None:
    """Same din mein account ne kitne alag receivers ko bheja."""
    sent = df_local[df_local["nameOrig"] == account_id]
    if sent.empty:
        return None
    per_day = sent.groupby("day")["nameDest"].nunique()
    max_day, max_count = per_day.idxmax(), per_day.max()
    if max_count >= FAN_DEGREE_THRESHOLD:
        return {"pattern": "fan_out", "day": int(max_day), "unique_receivers": int(max_count)}
    return None


def detect_fan_in(df_local: pd.DataFrame, account_id: str) -> dict | None:
    """Same din mein account ne kitne alag senders se receive kiya."""
    received = df_local[df_local["nameDest"] == account_id]
    if received.empty:
        return None
    per_day = received.groupby("day")["nameOrig"].nunique()
    max_day, max_count = per_day.idxmax(), per_day.max()
    if max_count >= FAN_DEGREE_THRESHOLD:
        return {"pattern": "fan_in", "day": int(max_day), "unique_senders": int(max_count)}
    return None


def detect_structuring(df_local: pd.DataFrame, account_id: str) -> dict | None:
    """RBI structuring zone (₹40k-49,999) mein baar baar transactions."""
    sent = df_local[df_local["nameOrig"] == account_id]
    structured = sent[
        (sent["amount_inr"] >= STRUCTURING_MIN) & (sent["amount_inr"] <= STRUCTURING_MAX)
    ]
    if len(structured) >= STRUCTURING_MIN_COUNT:
        return {"pattern": "structuring", "count": len(structured)}
    return None


def score_graph(account_id: str, new_txn: dict = None, batch_txns: list = None) -> dict:
    G_local, df_local, has_history = build_local_subgraph(account_id, new_txn, batch_txns)

    if not has_history:
        return {"graph_score": 0.0, "flags": [], "has_history": False}

    flags = []
    score = 0.0

    cycles = detect_round_trip(G_local)
    if cycles:
        score += 1.0
        flags.append({"pattern": "round_trip", "count": len(cycles)})

    fan_out = detect_fan_out(df_local, account_id)
    if fan_out:
        score += 0.9
        flags.append(fan_out)

    fan_in = detect_fan_in(df_local, account_id)
    if fan_in:
        score += 0.9
        flags.append(fan_in)

    structuring = detect_structuring(df_local, account_id)
    if structuring:
        score += 0.5
        flags.append(structuring)

    return {
        "graph_score": round(min(score / 3.0, 1.0), 4),
        "flags": flags,
        "has_history": True,
    }