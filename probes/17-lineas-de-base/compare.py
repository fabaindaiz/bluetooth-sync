"""Experiment 17's baselines, offline: the same stereo through each render, measured the same way.

Renders on the `auto` ring of 3 (s0 at -60°, s1 at +60°, s2 at 180°): stereo alone (s2 silent),
the motor's classic, spatial and "frente intacto" (the real `Motor`, decorrelator included), and
FFmpeg's `surround` upmix (5.1 folded to the three: FL + FC/√2, FR + FC/√2, (BL + BR)/√2; LFE
left out). Numbers, past the first second:

- `front_corr`: peak of the normalised cross-correlation of s0 with L and s1 with R (1: the front
  is the stereo untouched; "never worse than stereo" in front);
- `rear_db`: s2's energy against the mean of the front pair (how much reaches the back);
- `front_rear_coh`: peak normalised cross-correlation between the front pair and s2, above 200 Hz (low: the back
  is different content, which is what envelops; high: a copy, which the nearest speaker steals);
- `separation_db`: with a source hard left, s0 against s1.

SIMULADO: no room, no speakers. It says what each render sends, not how it sounds; that is the
blind A/B of experimentos/17. Usage (from host/, with hatch):

    hatch run python ../probes/17-lineas-de-base/compare.py [--wav song.wav] [--seconds 20]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

from aurasync import control
from aurasync.chain import ChainValues
from aurasync.config import Instalacion, Parlante
from aurasync.motor import Motor

SR = 48000
BLOCK = 4096
SKIP = SR
NAMES = ("s0", "s1", "s2")


def _installation() -> Instalacion:
    parlantes = []
    for i, angle in enumerate(control.auto_angles(3)):
        pan, ambience = control.role_from_angle(angle)
        parlantes.append(Parlante(NAMES[i], f"sink{i}", pan=pan, ambiente=ambience))
    return Instalacion(parlantes=parlantes)


def _motor(render: str, left: np.ndarray, right: np.ndarray) -> dict[str, np.ndarray]:
    m = Motor(_installation(), SR, semilla=1, chain=ChainValues.from_json({"spatial": {"algorithm": render}}))
    out: dict[str, list[np.ndarray]] = {n: [] for n in NAMES}
    for i in range(0, len(left), BLOCK):
        for name, x in m.procesar(left[i : i + BLOCK], right[i : i + BLOCK]).items():
            out[name].append(x)
    return {n: np.concatenate(v)[: len(left)] for n, v in out.items()}


def _ffmpeg_surround(left: np.ndarray, right: np.ndarray) -> dict[str, np.ndarray]:
    if shutil.which("ffmpeg") is None:
        msg = "ffmpeg is not installed"
        raise SystemExit(msg)
    stereo = np.column_stack([left, right]).astype("<f4").tobytes()
    raw = subprocess.run(
        [  # noqa: S607 - ffmpeg by name, as pw-play elsewhere
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "pipe:0",
            "-af", "surround=chl_out=5.1", "-f", "f32le", "-ac", "6", "pipe:1",
        ],
        input=stereo,
        capture_output=True,
        check=True,
    ).stdout
    six = np.frombuffer(raw, dtype="<f4").reshape(-1, 6).astype(float)  # FL FR FC LFE BL BR
    fl, fr, fc, _lfe, bl, br = six.T
    k = 1 / np.sqrt(2)
    n = len(left)

    def fit(x: np.ndarray) -> np.ndarray:
        return np.concatenate([x, np.zeros(max(0, n - len(x)))])[:n]

    return {"s0": fit(fl + k * fc), "s1": fit(fr + k * fc), "s2": fit(k * (bl + br))}


def _stereo(left: np.ndarray, right: np.ndarray) -> dict[str, np.ndarray]:
    return {"s0": left.copy(), "s1": right.copy(), "s2": np.zeros_like(left)}


RENDERS = {
    "estéreo solo": _stereo,
    "clásico": lambda left, right: _motor("classic", left, right),
    "espacial": lambda left, right: _motor("spatial", left, right),
    "frente intacto": lambda left, right: _motor("front", left, right),
    "ffmpeg surround": _ffmpeg_surround,
}


def _peak_corr(a: np.ndarray, b: np.ndarray, max_lag: int = 4096) -> float:
    a, b = a[SKIP:], b[SKIP:]
    n = min(len(a), len(b))
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    denom = np.sqrt((a @ a) * (b @ b))
    if denom < 1e-20:
        return 0.0
    size = 1 << (2 * n - 1).bit_length()
    xc = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)
    lags = np.concatenate([xc[: max_lag + 1], xc[-max_lag:]])
    return float(np.max(np.abs(lags)) / denom)


def _above(x: np.ndarray, hz: float = 200.0) -> np.ndarray:
    """`x` without what is below `hz` (brick-wall, offline): a bass sent to every speaker on purpose
    would otherwise make any two of them look like copies."""
    spectrum = np.fft.rfft(x)
    spectrum[: int(hz * len(x) / SR)] = 0.0
    return np.fft.irfft(spectrum, len(x))


def front_rear_coherence(out: dict[str, np.ndarray]) -> float:
    rear = _above(out["s2"])
    return max(_peak_corr(_above(out["s0"]), rear), _peak_corr(_above(out["s1"]), rear))


def _energy(x: np.ndarray) -> float:
    return float(x[SKIP:] @ x[SKIP:])


def _db(a: float, b: float) -> float:
    return 10 * np.log10(max(a, 1e-30) / max(b, 1e-30))


def measure(render, left: np.ndarray, right: np.ndarray, side: np.ndarray) -> dict[str, float]:
    out = render(left, right)
    front = (_energy(out["s0"]) + _energy(out["s1"])) / 2
    hard_left = render(side, np.zeros_like(side))
    return {
        "front_corr": round(min(_peak_corr(out["s0"], left), _peak_corr(out["s1"], right)), 3),
        "rear_db": round(_db(_energy(out["s2"]), front), 1) if _energy(out["s2"]) > 0 else float("-inf"),
        "front_rear_coh": round(front_rear_coherence(out), 3),
        "separation_db": round(min(_db(_energy(hard_left["s0"]), _energy(hard_left["s1"])), 60.0), 1),
    }


def write_multichannel(path: Path, out: dict[str, np.ndarray]) -> None:
    """One channel per speaker, in NAMES order, 48 kHz 16-bit: what aurasync.multichannel reads."""
    data = (np.clip(np.column_stack([out[n] for n in NAMES]), -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(len(NAMES))
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


def _read_wav(path: Path, seconds: float) -> tuple[np.ndarray, np.ndarray]:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SR or w.getnchannels() != 2 or w.getsampwidth() != 2:  # noqa: PLR2004
            msg = f"{path}: needs 48 kHz, 16-bit stereo (convert with ffmpeg -ar 48000 -ac 2 -sample_fmt s16)"
            raise SystemExit(msg)
        frames = w.readframes(int(seconds * SR))
    x = np.frombuffer(frames, dtype="<i2").reshape(-1, 2).astype(float) / 32768
    return x[:, 0] * 0.5, x[:, 1] * 0.5


def _synthetic(seconds: float, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """A centre source and independent room on each side, as spatial_docs' `music`."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    common = rng.standard_normal(n)
    return 0.1 * (common + 0.6 * rng.standard_normal(n)), 0.1 * (common + 0.6 * rng.standard_normal(n))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--wav", type=Path, help="48 kHz 16-bit stereo; by default a synthetic signal")
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument(
        "--out",
        type=Path,
        help="write the outside renders (stereo alone, FFmpeg) as 3-channel WAVs, to play with the panel's "
        "«render multicanal» source; the motor's own modes are heard by playing the song itself",
    )
    args = parser.parse_args()
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        song_l, song_r = _read_wav(args.wav, args.seconds) if args.wav else _synthetic(args.seconds)
        for name, render in (("estereo-solo", _stereo), ("ffmpeg-surround", _ffmpeg_surround)):
            write_multichannel(args.out / f"{name}.wav", render(song_l, song_r))
    left, right = _read_wav(args.wav, args.seconds) if args.wav else _synthetic(args.seconds)
    side = np.random.default_rng(9).standard_normal(len(left)) * 0.1
    source = args.wav.name if args.wav else "sintética (centro + sala independiente por lado)"
    print(f"Entrada: {source}, {len(left) / SR:.1f} s. SIMULADO.\n")  # noqa: T201
    print("| Render | front_corr | rear_db | front_rear_coh | separation_db |")  # noqa: T201
    print("|---|---|---|---|---|")  # noqa: T201
    for name, render in RENDERS.items():
        m = measure(render, left, right, side)
        print(f"| {name} | {m['front_corr']} | {m['rear_db']} | {m['front_rear_coh']} | {m['separation_db']} |")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
