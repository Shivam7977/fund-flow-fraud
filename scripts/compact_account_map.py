"""
One-time script: data/account_type_map.json (70MB) -> compact files.

Run from repo root:   python scripts/compact_account_map.py

Output:
  data/account_type_map.npz       (sorted int keys + type index, non-savings only)
  data/account_type_extras.json   (non C/M<digits> IDs, usually tiny/empty)

"savings" entries drop kiye jaate hain kyunki ml_engine ka default hi "savings" hai,
isliye lookup result bilkul same rehta hai (script end mein sab verify karta hai).
"""
import json
import random
import re
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "data" / "account_type_map.json"
OUT_NPZ = BASE / "data" / "account_type_map.npz"
OUT_EXTRAS = BASE / "data" / "account_type_extras.json"
DEFAULT = "savings"
ID_RE = re.compile(r"^([CM])(\d+)$")


def encode(acc):
    m = ID_RE.match(acc)
    if not m or str(int(m.group(2))) != m.group(2):
        return None
    return int(m.group(2)) * 2 + (1 if m.group(1) == "M" else 0)


with open(SRC) as f:
    full = json.load(f)

counts = {}
for t in full.values():
    counts[t] = counts.get(t, 0) + 1
print(f"Total accounts: {len(full):,}")
print("By type:", counts)

type_names = sorted({t for t in full.values() if t != DEFAULT})
idx = {t: i for i, t in enumerate(type_names)}

keys, vals, extras = [], [], {}
for acc, t in full.items():
    if t == DEFAULT:
        continue
    k = encode(acc)
    if k is None:
        extras[acc] = t
    else:
        keys.append(k)
        vals.append(idx[t])

keys = np.array(keys, dtype=np.int64)
vals = np.array(vals, dtype=np.uint8)
order = np.argsort(keys)
keys, vals = keys[order], vals[order]
assert len(np.unique(keys)) == len(keys), "duplicate encoded keys"

np.savez_compressed(OUT_NPZ, keys=keys, vals=vals, type_names=np.array(type_names))
with open(OUT_EXTRAS, "w") as f:
    json.dump(extras, f, separators=(",", ":"))


def lookup(acc):
    if acc in extras:
        return extras[acc]
    k = encode(acc)
    if k is None:
        return DEFAULT
    i = int(np.searchsorted(keys, k))
    if i < len(keys) and keys[i] == k:
        return type_names[int(vals[i])]
    return DEFAULT


bad = [a for a, t in full.items() if lookup(a) != t]
assert not bad, f"MISMATCH on {len(bad)} accounts, e.g. {bad[:5]}"
print("Verified: compact lookup == original for ALL accounts")
print(f"Stored (non-savings): {len(keys):,} | extras: {len(extras):,}")
print(f"{OUT_NPZ.name}: {OUT_NPZ.stat().st_size/1e6:.1f} MB | {OUT_EXTRAS.name}: {OUT_EXTRAS.stat().st_size/1e3:.1f} KB")