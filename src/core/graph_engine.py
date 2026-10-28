import networkx as nx
import pandas as pd
from core.db import get_account_history

ROUND_TRIP_MAX_LENGTH = 4
FAN_DEGREE_THRESHOLD = 10
STRUCTURING_MIN = 40000
STRUCTURING_MAX = 49999
STRUCTURING_MIN_COUNT = 3

# Time bound: har pattern sirf ek WINDOW_HOURS (24h) ke andar ginta hai.
# Time = day*24 + hour. Agar CSV mein sirf date hai (hour nahi), hour 0 default
# hota hai, to 24h window (strict <) apne aap "same date" ban jaata hai.
# Agar date bhi nahi hai, saari rows day 0 par hoti hain -> window kuch filter
# nahi kar sakti (documented limitation).
WINDOW_HOURS = 24

# Fix 1: har pattern ki apni real severity — divide-by-3 jaisa dilution nahi.
# Ek akela strong pattern (jaise structuring) bhi apni asli seriousness dikhayega.
PATTERN_SEVERITY = {
    "round_trip": 0.9,    # sabse serious — layering ka classic sign
    "structuring": 0.8,   # explicit RBI/CTR evasion pattern
    "fan_out": 0.7,
    "fan_in": 0.7,
}


def _abs_hours(day, hour) -> float:
    try:
        return float(day or 0) * 24 + float(hour or 0)
    except (TypeError, ValueError):
        return 0.0


def _windows(df: pd.DataFrame, anchor):
    """
    Har possible WINDOW_HOURS-wide window ke rows yield karta hai. Agar anchor
    (naye transaction ka time) diya hai, to sirf wahi windows jo anchor ko
    contain karti hain — taaki purana, ab-irrelevant pattern har naye
    transaction ko flag na kare, aur batch ka order sorted hona zaroori na ho.
    """
    if df.empty:
        return
    starts = sorted(df["t"].unique())
    for s in starts:
        if anchor is not None and not (anchor - WINDOW_HOURS < s <= anchor):
            continue
        yield df[(df["t"] >= s) & (df["t"] < s + WINDOW_HOURS)]


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
        "t": _abs_hours(h["day"], h["hour"]),
    } for h in history]

    rows += [{
        "nameOrig": t["nameOrig"], "nameDest": t["nameDest"],
        "amount_inr": t["amount_inr"], "day": t.get("day", 0), "hour": t.get("hour", 0),
        "t": _abs_hours(t.get("day", 0), t.get("hour", 0)),
    } for t in batch_rows]

    if new_txn:
        rows.append({
            "nameOrig": new_txn["nameOrig"], "nameDest": new_txn["nameDest"],
            "amount_inr": new_txn["amount_inr"],
            "day": new_txn.get("day", 0), "hour": new_txn.get("hour", 0),
            "t": _abs_hours(new_txn.get("day", 0), new_txn.get("hour", 0)),
        })

    if not rows:
        return nx.DiGraph(), pd.DataFrame(), has_history

    df_local = pd.DataFrame(rows)
    G_local = nx.from_pandas_edgelist(
        df_local, source="nameOrig", target="nameDest",
        edge_attr=["amount_inr", "day", "hour"], create_using=nx.DiGraph()
    )
    return G_local, df_local, has_history   # 👈 teesra value return karo


def build_batch_graph(new_txn: dict = None, batch_txns: list = None) -> nx.DiGraph:
    """
    Round-trip (multi-hop cycle, A->B->C->A) detection ke liye poora batch ka
    multi-account graph banata hai — ek account ka star-subgraph nahi, balki
    saare accounts ke beech ka actual network. Sirf batch_txns (isi file-
    upload/batch request ke andar ka data, already memory mein) use karta
    hai, koi extra DB call nahi. Isliye DB-persisted, alag-alag samay par
    hui cross-account history se bana round-trip isse nahi pakda jaata —
    yeh ek documented limitation hai.
    """
    rows = list(batch_txns or [])
    if new_txn:
        rows.append(new_txn)
    if not rows:
        return nx.DiGraph()
    df = pd.DataFrame(rows)
    if new_txn:
        # Time bound: sirf naye txn ke WINDOW_HOURS ke andar ki edges se cycle banna chahiye.
        df["t"] = [_abs_hours(r.get("day", 0), r.get("hour", 0)) for r in rows]
        t_new = _abs_hours(new_txn.get("day", 0), new_txn.get("hour", 0))
        df = df[(df["t"] - t_new).abs() < WINDOW_HOURS]
    return nx.from_pandas_edgelist(df, source="nameOrig", target="nameDest", create_using=nx.DiGraph())


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


def detect_fan_out(df_local: pd.DataFrame, account_id: str, anchor=None) -> dict | None:
    """WINDOW_HOURS ke andar account ne kitne alag receivers ko bheja."""
    sent = df_local[df_local["nameOrig"] == account_id]
    max_count = max((w["nameDest"].nunique() for w in _windows(sent, anchor)), default=0)
    if max_count >= FAN_DEGREE_THRESHOLD:
        return {"pattern": "fan_out", "unique_receivers": int(max_count), "window_hours": WINDOW_HOURS}
    return None


def detect_fan_in(df_local: pd.DataFrame, account_id: str, anchor=None) -> dict | None:
    """WINDOW_HOURS ke andar account ne kitne alag senders se receive kiya."""
    received = df_local[df_local["nameDest"] == account_id]
    max_count = max((w["nameOrig"].nunique() for w in _windows(received, anchor)), default=0)
    if max_count >= FAN_DEGREE_THRESHOLD:
        return {"pattern": "fan_in", "unique_senders": int(max_count), "window_hours": WINDOW_HOURS}
    return None


def detect_structuring(df_local: pd.DataFrame, account_id: str, anchor=None) -> dict | None:
    """RBI structuring zone (₹40k-49,999) mein WINDOW_HOURS ke andar baar baar transactions."""
    sent = df_local[df_local["nameOrig"] == account_id]
    structured = sent[
        (sent["amount_inr"] >= STRUCTURING_MIN) & (sent["amount_inr"] <= STRUCTURING_MAX)
    ]
    max_count = max((len(w) for w in _windows(structured, anchor)), default=0)
    if max_count >= STRUCTURING_MIN_COUNT:
        return {"pattern": "structuring", "count": int(max_count), "window_hours": WINDOW_HOURS}
    return None


def score_graph(account_id: str, new_txn: dict = None, batch_txns: list = None) -> dict:
    G_local, df_local, has_history = build_local_subgraph(account_id, new_txn, batch_txns)
    anchor = _abs_hours(new_txn.get("day", 0), new_txn.get("hour", 0)) if new_txn else None

    flags = []
    severities = []

    if has_history:
        # Fix (multi-hop round-trip): G_local sirf account_id ka star-subgraph
        # hai (sirf wahi edges jo isi account ko touch karte hain) — isme
        # A->B->C->A jaisa 3-node cycle KABHI nahi ban sakta, kyunki beech ka
        # B->C edge kisi aur account se dikh hi nahi raha. Isliye round-trip
        # ko poore batch ke multi-account graph (build_batch_graph) pe check
        # karte hain, phir sirf un cycles ko ginte hain jisme yeh account
        # khud shamil hai.
        G_batch = build_batch_graph(new_txn, batch_txns)
        cycles = [c for c in detect_round_trip(G_batch) if account_id in c]
        if cycles:
            flags.append({"pattern": "round_trip", "count": len(cycles)})
            severities.append(PATTERN_SEVERITY["round_trip"])

        fan_out = detect_fan_out(df_local, account_id, anchor)
        if fan_out:
            flags.append(fan_out)
            severities.append(PATTERN_SEVERITY["fan_out"])

        structuring = detect_structuring(df_local, account_id, anchor)
        if structuring:
            flags.append(structuring)
            severities.append(PATTERN_SEVERITY["structuring"])

    # Fix (fan-in): fan_in ek RECEIVER-side pattern hai ("kitne alag senders
    # se ek account paisa le raha hai"), lekin score_graph() hamesha
    # transaction ke SENDER (account_id) ke liye call hota hai — isliye
    # receiver ka apna accumulating fan-in kabhi check hi nahi hota tha,
    # chahe woh account sirf receive hi karta ho (jaise ek mule-collection
    # account jo khud kabhi sender na bana ho). Sender ki apni history se
    # independent check karte hain. NOTE: isse ek extra get_account_history()
    # DB call lagta hai har prediction mein (receiver ke liye) — yeh
    # jaanboojh kar accept kiya gaya cost hai, taaki fan-in genuinely kaam kare.
    if new_txn and new_txn.get("nameDest"):
        receiver_id = new_txn["nameDest"]
        _, df_recv, recv_has_history = build_local_subgraph(receiver_id, new_txn, batch_txns)
        if recv_has_history:
            fan_in = detect_fan_in(df_recv, receiver_id, anchor)
            if fan_in:
                flags.append(fan_in)
                severities.append(PATTERN_SEVERITY["fan_in"])

    # Fix 1: sabse severe pattern ki value lo (max), sabko average karke dilute mat karo.
    # Ek akela strong pattern (jaise structuring) bhi apni asli severity dikhayega.
    graph_score = max(severities) if severities else 0.0

    # has_history sirf SENDER ki apni history ko reflect karta hai (jaisa
    # pehle tha, routes/predict.py ka _combine() isi par based hai). Agar
    # sender ke paas khud koi history nahi thi lekin fan-in ne kuch pakda
    # (flags non-empty), to has_history ko bhi True kar dete hain — warna
    # _combine() ka "has_history and graph_score > 0" check fail ho jaata
    # aur fan-in ka pakda hua signal final score mein kabhi use hi na hota.
    has_history = has_history or bool(flags)

    return {
        "graph_score": round(graph_score, 4),
        "flags": flags,
        "has_history": has_history,
    }