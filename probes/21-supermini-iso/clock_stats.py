"""Statistics of a clockprobe recording (readclock.py output: '<host monotonic> <line>').

- LF clock vs HFXO, per LF second: mean, std, min/max, and the mean over windows of 4, 10 and 60 s
  (how much a short measurement can be off).
- Steps: how often consecutive seconds differ by more than 20 ppm (RC calibration jumps).
- HFXO vs the PC: cumulative HF ticks against the host time of each line (least squares). The host
  time is when the line arrived over USB, so only long spans mean anything.

Usage: python clock_stats.py <recording.txt> [skip_records]
"""

from __future__ import annotations

import json
import statistics
import sys


def window_means(values: list[float], size: int) -> list[float]:
    return [statistics.fmean(values[i : i + size]) for i in range(0, len(values) - size + 1, size)]


def main(path: str, skip: int) -> None:
    ppm, host, raw, total, crystal = [], [], [], [], []
    base, prev = 0, None
    for line in open(path):
        parts = line.split()
        if len(parts) >= 6 and parts[1] == 'S':
            if skip > 0:  # the first lines arrive in a burst when the port opens
                skip -= 1
                continue
            ppm.append(int(parts[4]) / 100)
            value = int(parts[5])
            if prev is not None and value < prev:
                base += 2**32
            prev = value
            total.append(value + base)
            host.append(float(parts[0]))
            if len(parts) >= 8 and parts[6] == 'R':  # readclock.py also stamps CLOCK_MONOTONIC_RAW
                raw.append(float(parts[7]))
        elif len(parts) >= 6 and parts[1] == 'X':
            crystal.append((int(parts[2]), int(parts[3]), int(parts[4]) / 100, parts[5]))
    steps = [abs(b - a) for a, b in zip(ppm, ppm[1:])]
    result = {
        'seconds': len(ppm),
        'lf_vs_hfxo_ppm': {
            'mean': round(statistics.fmean(ppm), 2),
            'std': round(statistics.pstdev(ppm), 2),
            'min': min(ppm),
            'max': max(ppm),
        },
        'window_mean_spread_ppm': {
            str(size): round(max(m) - min(m), 2) if len(m := window_means(ppm, size)) > 1 else None
            for size in (4, 10, 60)
        },
        'steps_over_20ppm': sum(step > 20 for step in steps),
        'median_step_ppm': round(statistics.median(steps), 2) if steps else None,
        'crystal_test_lines': sorted(set(crystal)),
    }
    def hf_vs(clock: list[float]) -> float:
        ys = [t / 16.0 for t in total]  # HF ticks -> µs
        mx, my = statistics.fmean(clock), statistics.fmean(ys)
        slope = sum((x - mx) * (y - my) for x, y in zip(clock, ys)) / sum((x - mx) ** 2 for x in clock)
        return round((slope / 1e6 - 1) * 1e6, 2)

    if len(host) > 60:
        result['hfxo_vs_pc_ppm'] = hf_vs(host)  # CLOCK_MONOTONIC: NTP slews it
        result['span_s'] = round(host[-1] - host[0], 1)
    if len(raw) == len(host) and len(raw) > 60:
        result['hfxo_vs_pc_raw_ppm'] = hf_vs(raw)  # CLOCK_MONOTONIC_RAW: the PC's crystal, untouched
    print(json.dumps(result))


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0)
