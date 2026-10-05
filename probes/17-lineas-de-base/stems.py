"""Experiment 17's stems pilot, offline (research/14 §6, option B): different content in each
speaker, from a song separated with HTDemucs.

The song is separated into vocals, drums, bass and other; then, on the `auto` ring of 3:
s0 = vocals L + drums L + bass/√3, s1 = vocals R + drums R + bass/√3, s2 = other (L + R)/√2 +
bass/√3. The front keeps what carries the image (voice and rhythm); the back gets what fills the
room (pads, reverb, guitars). It writes one WAV per speaker and measures them as `compare.py`.

Runs in the `ml` image (PyTorch CPU and Demucs; docs/research/08 §6.2.1), from host/:

    container/aurasync-container ml python ../probes/17-lineas-de-base/stems.py song.wav out/

The first run downloads the model (~80 MB) into the image's home volume. SIMULADO: what each speaker
gets, not how it sounds; listening is the A/B of experimentos/17.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import compare  # noqa: E402 - the same measures, next to this file

SR = compare.SR
STEMS = ("vocals", "drums", "bass", "other")


def _read(path: Path) -> np.ndarray:
    """(samples, 2) floats at 48 kHz, through ffmpeg (Demucs writes 44.1 kHz)."""
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-ar", str(SR), "-ac", "2", "-f", "f32le", "pipe:1"],  # noqa: S607
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype="<f4").reshape(-1, 2).astype(float)


def _write(path: Path, x: np.ndarray) -> None:
    pcm = (np.clip(x, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def separate(song: Path, out: Path) -> dict[str, np.ndarray]:
    subprocess.run([sys.executable, "-m", "demucs", "-n", "htdemucs", "-o", str(out), str(song)], check=True)
    folder = out / "htdemucs" / song.stem
    return {s: _read(folder / f"{s}.wav") for s in STEMS}


def render(stems: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    n = min(len(x) for x in stems.values())
    v, d, b, o = (stems[s][:n] for s in STEMS)
    bass = b.mean(axis=1) * np.sqrt(2) / np.sqrt(3)
    return {
        "s0": v[:, 0] + d[:, 0] + bass,
        "s1": v[:, 1] + d[:, 1] + bass,
        "s2": (o[:, 0] + o[:, 1]) / np.sqrt(2) + bass,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("song", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    stems = separate(args.song, args.out)
    mix = _read(args.song)
    speakers = render(stems)
    n = len(speakers["s0"])
    left, right = mix[:n, 0], mix[:n, 1]
    for name, x in speakers.items():
        _write(args.out / f"{name}.wav", x)
    # The three together, to play with the panel's «render multicanal» source (aurasync.multichannel).
    compare.write_multichannel(args.out / "pistas.wav", speakers)
    front = (compare._energy(speakers["s0"]) + compare._energy(speakers["s1"])) / 2  # noqa: SLF001
    row = {
        "front_corr": round(min(compare._peak_corr(speakers["s0"], left), compare._peak_corr(speakers["s1"], right)), 3),  # noqa: SLF001
        "rear_db": round(compare._db(compare._energy(speakers["s2"]), front), 1),  # noqa: SLF001
        "front_rear_coh": round(compare.front_rear_coherence(speakers), 3),
    }
    print(f"Entrada: {args.song.name}, {n / SR:.1f} s. SIMULADO.")  # noqa: T201
    print("| Render | front_corr | rear_db | front_rear_coh |")  # noqa: T201
    print("|---|---|---|---|")  # noqa: T201
    print(f"| pistas (HTDemucs) | {row['front_corr']} | {row['rear_db']} | {row['front_rear_coh']} |")  # noqa: T201
    print(f"Un WAV por parlante en {args.out}/s0.wav, s1.wav, s2.wav")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
