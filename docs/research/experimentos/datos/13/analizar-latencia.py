"""Latency of the PoC from a 4-channel recording of the test sink's monitor:
AUX3 = the player's FL straight into the sink (reference), AUX0..AUX2 = the PoC's outputs.
Each click in AUX3 is paired with the first click at or after it in each other channel."""
import json, sys, numpy as np
def read_wav(p):
    b = open(p, 'rb').read()
    i = 12; ch = None
    while i < len(b):
        cid, size = b[i:i+4], int.from_bytes(b[i+4:i+8], 'little')
        if cid == b'fmt ':
            ch = int.from_bytes(b[i+10:i+12], 'little')
        if cid == b'data':
            d = np.frombuffer(b[i+8:i+8+size], dtype='<f4')
            return d[: len(d) // ch * ch].reshape(-1, ch)
        i += 8 + size + (size & 1)
x = read_wav(sys.argv[1])
def onsets(c):
    idx = np.flatnonzero(np.abs(x[:, c]) > 0.02)
    if len(idx) == 0: return idx
    keep = np.concatenate(([True], np.diff(idx) > 2400))
    return idx[keep]
ref = onsets(3)
import os; out = {"file": os.path.basename(sys.argv[1]), "recorded_until": __import__("time").strftime("%F %T", __import__("time").localtime(os.path.getmtime(sys.argv[1]))), "frames": len(x), "ref_clicks": len(ref)}
for c in range(3):
    o = onsets(c)
    d = []
    for r in ref:
        j = np.searchsorted(o, r)
        if j < len(o) and o[j] - r < 24000: d.append(int(o[j] - r))
    d = np.array(d)
    out[f"AUX{c}"] = {"pairs": len(d), "min": int(d.min()) if len(d) else None, "max": int(d.max()) if len(d) else None,
                      "median_samples": float(np.median(d)) if len(d) else None,
                      "median_ms": round(float(np.median(d)) / 48, 3) if len(d) else None,
                      "values": sorted(set(d.tolist()))}
print(json.dumps(out))
