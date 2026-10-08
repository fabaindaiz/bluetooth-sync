"""Analysis of experiment 23's captures and state log.

Run from host/ with the project's environment (it imports aurasync.dsp.eq for the expected curves):

    cd host && hatch run python ../probes/24-ab-monitor/analizar.py [DATA] [OUT.json]

For each capture: the delay from input to output, the level of each, and the transfer function
input → output in third-octave bands as an energy ratio out/in (Welch, both channels averaged to
mono), with H1 and the coherence as diagnostics. Then the differences between captures that differ in one variable, next to what
eq.limited says they should be, and against the repetition of the base (what is noise). From the
log: motor_ms, cuts, xruns and the monitor's counters per engine and configuration.
"""

from __future__ import annotations

import json
import os
import struct
import sys

import numpy as np

from aurasync.dsp import eq

SR = 48000
NPERSEG = 16384
HERE = os.path.dirname(os.path.abspath(__file__))


def read_wav(path: str) -> np.ndarray:
    raw = open(path, "rb").read()
    pos, fmt, channels = 12, None, 2
    while pos + 8 <= len(raw):
        cid, size = raw[pos : pos + 4], struct.unpack_from("<I", raw, pos + 4)[0]
        if cid == b"fmt ":
            fmt, channels = struct.unpack_from("<HH", raw, pos + 8)
        elif cid == b"data":
            # pw-record may leave the size at 0 or 0xffffffff when it is stopped: read to the end.
            end = len(raw) if size in (0, 0xFFFFFFFF) or pos + 8 + size > len(raw) else pos + 8 + size
            data = np.frombuffer(raw[pos + 8 : end - (end - pos - 8) % (4 * channels)], dtype="<f4")
            return data.reshape(-1, channels).astype(np.float64)
        pos += 8 + size + (size & 1)
    raise ValueError(f"{path}: no data chunk (fmt {fmt})")


def delay(x: np.ndarray, y: np.ndarray, max_s: float = 2.0) -> int:
    """Samples y lags x, from the cross-correlation of the first 15 s."""
    n = min(len(x), len(y), 15 * SR)
    a, b = x[:n] - x[:n].mean(), y[:n] - y[:n].mean()
    size = 1 << int(np.ceil(np.log2(2 * n)))
    corr = np.fft.irfft(np.fft.rfft(b, size) * np.conj(np.fft.rfft(a, size)), size)
    lags = np.concatenate([corr[: int(max_s * SR)]])
    return int(np.argmax(np.abs(lags)))


def welch(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    win = np.hanning(NPERSEG)
    step = NPERSEG // 2
    sxx = syy = sxy = 0
    for start in range(0, min(len(x), len(y)) - NPERSEG, step):
        fx = np.fft.rfft(win * x[start : start + NPERSEG])
        fy = np.fft.rfft(win * y[start : start + NPERSEG])
        sxx = sxx + np.abs(fx) ** 2
        syy = syy + np.abs(fy) ** 2
        sxy = sxy + fy * np.conj(fx)
    freqs = np.fft.rfftfreq(NPERSEG, 1 / SR)
    return freqs, sxx, syy, sxy


def bands(freqs: np.ndarray, sxx: np.ndarray, syy: np.ndarray, sxy: np.ndarray) -> tuple[list, list, list]:
    """Per third: the energy ratio out/in (what is compared), the H1 gain and the coherence (only as a
    diagnostic: with music through a time-varying chain the coherence is low and H1 reads low)."""
    gain, h1, coherence = [], [], []
    for f in eq.THIRDS:
        sel = (freqs >= f / 2 ** (1 / 6)) & (freqs < f * 2 ** (1 / 6))
        if not sel.any():
            gain.append(None)
            h1.append(None)
            coherence.append(None)
            continue
        x, y, xy = sxx[sel].sum(), syy[sel].sum(), np.abs(sxy[sel].sum())
        gain.append(round(float(10 * np.log10(max(y / x, 1e-24))), 2))
        h1.append(round(float(20 * np.log10(max(xy / x, 1e-12))), 2))
        coherence.append(round(float(xy**2 / (x * y)), 3))
    return gain, h1, coherence


def analyse_capture(data: str, label: str) -> dict:
    x = read_wav(os.path.join(data, "wav", f"{label}-in.wav")).mean(axis=1)
    y = read_wav(os.path.join(data, "wav", f"{label}-out.wav")).mean(axis=1)
    lag = delay(x, y)
    x2, y2 = x[: len(x) - lag] if lag else x, y[lag:]
    n = min(len(x2), len(y2))
    gain, h1, coherence = bands(*welch(x2[:n], y2[:n]))
    rms = lambda v: round(float(20 * np.log10(max(np.sqrt(np.mean(v**2)), 1e-12))), 2)  # noqa: E731
    return {
        "seconds": round(n / SR, 1),
        "lag_ms": round(lag / SR * 1000, 1),
        "in_dbfs": rms(x2[:n]),
        "out_dbfs": rms(y2[:n]),
        "gain_db": gain,
        "h1_db": h1,
        "coherence": coherence,
    }


def diff(a: dict, b: dict) -> list:
    return [None if p is None or q is None else round(q - p, 2) for p, q in zip(a["gain_db"], b["gain_db"])]


def expected(curve: list[float]) -> dict:
    full = eq.limited(curve, None, None)
    return {
        "ensayo-eq": [round(-v, 2) for v in full],
        "ensayo-presupuesto": [round(v - w, 2) for v, w in zip(eq.limited(curve, 3.0, 6.0), full)],
        "ensayo-agudos": [round(v - w, 2) for v, w in zip(eq.limited(curve, 0.0, 0.0), full)],
    }


def log_summary(data: str) -> dict:
    rows = [json.loads(line) for line in open(os.path.join(data, "registro.jsonl"))]
    marks = [r for r in rows if "mark" in r]
    states = [r for r in rows if "mark" not in r and "error" not in r]
    # Each state row gets the phase it falls in: the last mark before it.
    phase, out = "inicio", {}
    k = 0
    for r in states:
        while k < len(marks) and marks[k]["t"] <= r["t"]:
            m = marks[k]
            if m["mark"] in ("heavy_run",):
                phase = f"pesado-{m['engine']}"
            elif m["mark"] == "heavy_off":
                phase = "base"
            elif m["mark"] == "engine_set" and not phase.startswith("pesado"):
                phase = f"base-{m['engine']}"
            elif m["mark"] == "switches_start":
                phase = "cambios"
            k += 1
        engine = (r.get("engine") or {}).get("active")
        key = f"{phase}|{engine}"
        h = r.get("health") or {}
        mon = r.get("monitor") or {}
        g = out.setdefault(key, {"n": 0, "motor_ms": [], "first": r, "last": r})
        g["n"] += 1
        g["last"] = r
        if h.get("motor_ms") is not None:
            g["motor_ms"].append(h["motor_ms"])
    summary = {}
    for key, g in out.items():
        ms = np.array(g["motor_ms"]) if g["motor_ms"] else None
        first, last = g["first"], g["last"]
        mf, ml = first.get("monitor") or {}, last.get("monitor") or {}
        summary[key] = {
            "seconds": g["n"],
            "motor_ms_median": None if ms is None else round(float(np.median(ms)), 3),
            "motor_ms_p95": None if ms is None else round(float(np.percentile(ms, 95)), 3),
            "motor_ms_max": None if ms is None else round(float(ms.max()), 3),
            "budget_ms": (last.get("health") or {}).get("budget_ms"),
            "monitor_drops": [mf.get("drops"), ml.get("drops")],
            "monitor_refills": [mf.get("refills"), ml.get("refills")],
            "monitor_trims": [mf.get("trims"), ml.get("trims")],
            "xruns_last": (last.get("health") or {}).get("xruns"),
            "cuts_last": (last.get("health") or {}).get("cuts"),
        }
    return {"phases": summary, "marks": [{k: v for k, v in m.items() if k != "wiring"} for m in marks]}


def main() -> None:
    data = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.local/share/aurasync/ab-ensayo")
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(data, "analisis.json")
    sys.path.insert(0, HERE)
    import ab  # noqa: PLC0415

    labels = sorted({f[: -len("-in.wav")] for f in os.listdir(os.path.join(data, "wav")) if f.endswith("-in.wav")})
    captures = {label: analyse_capture(data, label) for label in labels}
    exp = expected(ab.test_curve())
    comparisons = {}
    for pair in ab.PAIRS:
        a, b = captures.get(f"{pair}-a"), captures.get(f"{pair}-b")
        if a and b:
            comparisons[pair] = {"measured": diff(a, b), "expected": exp[pair]}
    base, rep = captures.get("ensayo-eq-a"), captures.get("ensayo-eq-a-repeticion")
    if base and rep:
        comparisons["repeticion"] = {"measured": diff(base, rep), "expected": [0.0] * len(eq.THIRDS)}
    numpy_, rust = captures.get("motor-numpy"), captures.get("motor-rust")
    if numpy_ and rust:
        comparisons["motor"] = {"measured": diff(numpy_, rust), "expected": [0.0] * len(eq.THIRDS)}
    # The monitor matches its loudness to the input's (monitor.makeup_db), which shifts every band of
    # a capture alike: the shapes are compared with the mids (400 Hz–3.2 kHz, where the test curve is
    # flat) as the reference, and the broadband offset is reported apart.
    mids = [i for i, f in enumerate(eq.THIRDS) if 397 <= f <= 3175]
    for c in comparisons.values():
        offset = float(np.mean([c["measured"][i] - c["expected"][i] for i in mids if c["measured"][i] is not None]))
        c["offset_db"] = round(offset, 2)
        c["shape"] = [None if m is None else round(m - offset, 2) for m in c["measured"]]
        err = [abs(m - e) for m, e in zip(c["shape"], c["expected"]) if m is not None]
        c["error_db_median"] = round(float(np.median(err)), 2)
        c["error_db_max"] = round(float(np.max(err)), 2)
    result = {
        "thirds_hz": list(eq.THIRDS),
        "captures": captures,
        "comparisons": comparisons,
        "log": log_summary(data),
    }
    json.dump(result, open(out_path, "w"), indent=1, ensure_ascii=False)
    print(f"análisis: {out_path}")
    for name, c in comparisons.items():
        print(f"{name:20s} desplazamiento {c['offset_db']:+5.2f} dB; forma: error mediano {c['error_db_median']:5.2f} dB, "
              f"máximo {c['error_db_max']:5.2f} dB")
    for label, c in captures.items():
        print(f"{label:28s} retardo {c['lag_ms']:7.1f} ms  entrada {c['in_dbfs']:6.1f}  salida {c['out_dbfs']:6.1f} dBFS")
    for key, p in result["log"]["phases"].items():
        print(f"{key:24s} {p['seconds']:4d} s  motor {p['motor_ms_median']} ms (p95 {p['motor_ms_p95']})")


if __name__ == "__main__":
    main()
